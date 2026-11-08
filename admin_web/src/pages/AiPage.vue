<template>
  <PageHeader :icon="DataAnalysis" title="三方比对" desc="设备按网元汇总综资、现场、资产数据；ODF / IODF 机架独立核对" />
  <div class="toolbar room-query-toolbar">
    <el-form-item class="room-query-field" label="查询字段"><el-select v-model="roomQueryField" @change="roomQueryText=''">
      <el-option label="全部已完成机房" value="all" /><el-option label="机房名称" value="room_name" /><el-option label="领取人" value="assigned_name" />
    </el-select></el-form-item>
    <el-form-item class="room-query-text" label="查询内容"><el-input v-model="roomQueryText" clearable :disabled="roomQueryField==='all'" :placeholder="roomQueryField==='all'?'全部查询无需输入':roomQueryField==='room_name'?'请输入机房名称':'请输入领取人姓名'" @keyup.enter="queryRooms" /></el-form-item>
    <div class="toolbar-actions room-query-actions"><el-button type="primary" :loading="roomQueryLoading" @click="queryRooms">查询</el-button><el-button :disabled="!selectedIds.length" :loading="loading" @click="loadComparison">执行比对</el-button><el-button v-if="entityKind==='devices'" :disabled="!result || !!result.warnings.length || loading" @click="exportComparison">导出设备比对</el-button></div>
  </div>
  <div class="room-selection-summary">
    <strong>{{selectedIds.length ? `已选 ${selectedIds.length} 个机房` : '未选择机房'}}</strong>
    <el-tooltip v-if="selectedTasks.length" :content="selectedTasks.map(t=>t.room_name).join('、')" placement="top"><span class="selected-room-names">{{selectedTasks.map(t=>t.room_name).join('、')}}</span></el-tooltip>
    <span v-else>查询后勾选已完成盘点的机房，每个机房使用最近一次已完成记录。</span>
    <el-button v-if="selectedIds.length" link type="primary" :loading="roomQueryLoading" @click="queryRooms">重新选择</el-button>
  </div>
  <template v-if="result">
  <el-alert v-for="warning in result?.warnings || []" :key="warning" :title="warning" type="warning" :closable="false" show-icon class="notice" />
  <div v-if="result" class="asset-scope-note">
    <div class="asset-scope-heading">
      <strong>所选机房资产：<b>{{result.asset_counts.total}}</b> 个条码</strong>
      <el-button link type="primary" @click="assetScopeOpen=true">查看资产明细</el-button>
    </div>
    <div class="asset-scope-counts">
      <span>设备 <b>{{result.asset_counts.device - (result.asset_counts.cabinet || 0)}}</b></span>
      <span>传输综合柜 <b>{{result.asset_counts.cabinet || 0}}</b></span>
      <span>ODF / IODF 机架 <b>{{result.asset_counts.rack}}</b></span>
      <span>未关联资源条码 <b>{{result.asset_counts.unlinked}}</b></span>
    </div>
    <p class="asset-scope-hint">仅统计地点编码与所选机房一致的资产；以上均为条码数量，同一资源可有多个条码。</p>
  </div>
  <div v-if="result && entityKind==='devices'" class="metric-grid">
    <div v-for="m in metrics" :key="m.label" class="metric-card"><div class="metric-label">{{m.label}}</div><div class="metric-value">{{m.value}}</div></div>
  </div>
  <div class="toolbar comparison-toolbar">
    <div class="comparison-filters">
      <el-form-item class="comparison-keyword" label="关键字"><el-input v-model="keyword" clearable placeholder="资产条码、资源名称、机房" @input="page=1;rackPage=1;unlinkedPage=1" /></el-form-item>
      <el-form-item v-if="entityKind==='devices'" class="comparison-result-filter" label="结果筛选"><el-select v-model="filter" @change="page=1"><el-option label="全部" value="all" /><el-option label="问题 / 待处理" value="issues" /><el-option label="三方一致" value="consistent" /><el-option label="型号待判断" value="pending" /><el-option label="多条码人工比对" value="manual" /></el-select></el-form-item>
    </div>
    <div v-if="entityKind==='devices'" class="comparison-actions">
      <el-button v-if="permissions.updateBaseline" @click="sourceOpen=true" :disabled="!selectedIds.length">地点编码与综资更新</el-button>
      <div class="comparison-ai-actions">
        <el-button @click="openAiHistory">历史 AI 报告</el-button>
        <el-button v-if="permissions.configureAi" @click="aiSettingsOpen=true">AI 设置</el-button>
        <el-tooltip v-if="permissions.runAi" :disabled="!mixedBatches" content="AI 辅助分析暂需同一批次，请缩小机房选择范围。" placement="top"><span><el-button type="primary" plain :disabled="!result || !!result.warnings.length || !agentReady || mixedBatches" :loading="analyzing" @click="analyze">AI 辅助分析</el-button></span></el-tooltip>
      </div>
    </div>
  </div>
  <el-radio-group v-model="entityKind" class="comparison-tabs">
    <el-radio-button :label="`设备比对（${result?.groups.length || 0} 组）`" value="devices" /><el-radio-button :label="`机架核对（${result?.rack_comparison.length || 0} 组）`" value="racks" /><el-radio-button :label="`未关联资源条码（${result?.unlinked_assets.length || 0} 个）`" value="unlinked" />
  </el-radio-group>
  <template v-if="entityKind==='devices'">
    <SectionTitle :icon="Tickets" title="设备三系统比对" muted="先限定同一地点编码的机房，再以网元 / 资源名称关联，汇总综资、现场、资产三行；两处型号由管理员分别判断。" />
    <ThreeWayTable :groups="pageGroups">
      <template #actions="{group}"><div class="action-stack">
        <el-button link type="primary" :disabled="!!result?.warnings.length || group.pending_association" @click="judge(group)">{{group.manual?'人工比对':'判断型号'}}</el-button>
        <el-button link @click="detail(group)">查看问题</el-button>
        <el-button v-if="canCorrect(group)" link type="primary" @click="correct(group)">{{group.pending_association ? '补录条码 / 修正反馈' : '修正现场反馈'}}</el-button>
        <el-tag v-if="group.case && group.case.status !== 'done'" size="small" type="info">工单 #{{group.case.id}}</el-tag>
        <el-button v-else-if="permissions.writeGovernance && group.can_create_adjustment" link type="warning" @click="createCase(group)">确认问题并建单</el-button>
      </div></template>
    </ThreeWayTable>
    <el-pagination v-if="filtered.length" v-model:current-page="page" :page-size="30" :total="filtered.length" layout="total, prev, pager, next" class="pagination" />
    <SectionTitle :icon="Tickets" title="传输综合柜独立核对" muted="综资柜位从设备行的设备机架字段汇总；现场柜条码取最新有效设备反馈，单独与同地点资产柜记录核对。综资未提供柜条码，不作三方条码一致判断。" />
    <CabinetComparisonTable :groups="cabinetFiltered" />
  </template>
  <template v-else-if="entityKind==='racks'">
    <SectionTitle :icon="Tickets" title="ODF / IODF 机架核对" muted="汇总资产表和本次现场登记的 ODF / IODF；资产表有、现场未登记的机架也会展示。同名多条交管理员核实，机架不判断型号，综资基线待补。" />
    <div class="table-wrap"><el-table :data="rackPageRows" border stripe :max-height="660" empty-text="本次所选机房暂无资产或现场机架记录">
      <el-table-column prop="room_name" label="机房" min-width="170" show-overflow-tooltip />
      <el-table-column prop="rack_type" label="类型" width="85" />
      <el-table-column prop="resource_name" label="关联键：资源名称 / 机架编号" min-width="190" show-overflow-tooltip />
      <el-table-column prop="field_status" label="现场登记状态" min-width="190" />
      <el-table-column prop="field_barcode" label="现场资产条码" min-width="155" />
      <el-table-column prop="asset_barcodes" label="资产表条码" min-width="155"><template #default="{row}">{{row.asset_barcodes || '未找到'}}</template></el-table-column>
      <el-table-column prop="barcode_status" label="条码核对" min-width="160" />
      <el-table-column label="现场地点编码" min-width="135"><template #default="{row}">{{row.field_location_code || '未反馈'}}</template></el-table-column>
      <el-table-column prop="asset_location_codes" label="资产地点编码" min-width="135"><template #default="{row}">{{row.asset_location_codes || '未找到'}}</template></el-table-column>
      <el-table-column prop="location_status" label="地点核对" min-width="160" />
      <el-table-column prop="resource_status" label="综资状态" min-width="150" />
    </el-table></div>
    <el-pagination v-if="rackFiltered.length" v-model:current-page="rackPage" :page-size="30" :total="rackFiltered.length" layout="total, prev, pager, next" class="pagination" />
  </template>
  <template v-else>
    <SectionTitle :icon="Tickets" title="未关联资源条码" muted="资产信息表中资源名称为空，表示该条码尚未在综资关联资源；不据此判断现场是否使用。仅展示所选机房地点编码内的记录。" />
    <div class="table-wrap"><el-table :data="unlinkedPageRows" border stripe :max-height="660" empty-text="所选机房暂无未关联资源条码">
      <el-table-column prop="barcode" label="资产条码" min-width="180" /><el-table-column prop="location_code" label="地点编码" min-width="150" /><el-table-column prop="model" label="资产规格型号" min-width="200" />
      <el-table-column label="状态" min-width="180">综资未关联资源，待管理员核实</el-table-column>
    </el-table></div>
    <el-pagination v-if="unlinkedFiltered.length" v-model:current-page="unlinkedPage" :page-size="30" :total="unlinkedFiltered.length" layout="total, prev, pager, next" class="pagination" />
  </template>
  </template>
  <el-empty v-else :description="selectedIds.length ? '已选择机房，请点击执行比对查看结果。' : '请先查询并勾选已完成盘点的机房。'" />
  <el-dialog v-model="roomPickerOpen" title="选择已完成盘点的机房" width="min(1080px, 96vw)">
    <p class="source-note">查询到 {{roomCandidates.length}} 个机房，每个机房仅保留最近一次已完成记录。</p>
    <el-table ref="roomPickerTable" :data="roomCandidates" row-key="id" border stripe height="360" :header-cell-style="{textAlign:'center'}" empty-text="没有查到符合条件的已完成机房" @selection-change="roomDialogSelection=$event">
      <el-table-column type="selection" width="48" />
      <el-table-column prop="room_name" label="机房" min-width="300" show-overflow-tooltip />
      <el-table-column prop="assigned_name" label="领取人" width="110"><template #default="{row}">{{row.assigned_name || '-'}}</template></el-table-column>
      <el-table-column label="盘点完成时间" width="170"><template #default="{row}">{{formatTime(comparisonTaskTime(row))}}</template></el-table-column>
      <el-table-column prop="area_name" label="片区" width="115" />
      <el-table-column prop="room_code" label="地点编码" min-width="185" show-overflow-tooltip />
    </el-table>
    <template #footer><div class="room-picker-footer"><span>已勾选 {{roomDialogSelection.length}} 个机房</span><el-button @click="roomPickerOpen=false">取消</el-button><el-button type="primary" :disabled="!roomDialogSelection.length" @click="confirmRooms">确定并比对</el-button></div></template>
  </el-dialog>
  <el-dialog v-model="assetScopeOpen" title="所选机房资产条码明细" width="min(1200px, 96vw)">
    <p class="source-note">仅列出地点编码与所选机房一致的资产条码。其中 {{result?.asset_counts.named || 0}} 个条码已填写资源名称，涉及 {{result?.asset_counts.resource_names || 0}} 项资源；同一资源的多个条码均完整保留。设备、传输综合柜、ODF / IODF 机架和未关联资源四类条码数合计等于资产条码总数。</p>
    <el-table :data="result?.scoped_assets || []" border stripe :max-height="560">
      <el-table-column type="index" label="序号" width="65" />
      <el-table-column prop="barcode" label="资产条码" min-width="165" />
      <el-table-column label="查看入口" min-width="150"><template #default="{row}"><el-button link type="primary" @click="showAssetCategory(row.entity_kind,row.barcode)">{{row.entity_kind==='rack'?'机架核对':row.entity_kind==='unlinked'?'未关联资源条码':'设备比对'}}</el-button></template></el-table-column>
      <el-table-column label="资源名称" min-width="260"><template #default="{row}">{{row.name || '未关联资源'}}</template></el-table-column>
      <el-table-column prop="resource_category" label="资产表资源类别" min-width="150" />
      <el-table-column prop="asset_name" label="资产名称" min-width="150" />
      <el-table-column prop="location_code" label="地点编码" min-width="160" />
    </el-table>
  </el-dialog>
  <el-dialog v-model="detailOpen" title="问题与判断依据" width="min(950px, 94vw)">
    <template v-if="activeGroup">
      <p>{{activeGroup.room_name}} · {{activeGroup.barcode || '待补资产条码'}}</p>
      <el-alert v-if="activeGroup.consistent" title="当前三方一致" type="success" :closable="false" />
      <div v-for="problem in comparisonIssues(activeGroup)" :key="problem.title + problem.detail" class="problem-line">
        <strong>{{problem.title}}</strong><div v-if="problem.detail" class="problem-evidence">{{problem.detail}}</div>
      </div>
      <p class="source-note">资产版本 #{{activeGroup.versions.asset_batch_id}}；综资更新版本 {{JSON.stringify(activeGroup.versions.resources)}}</p>
    </template>
  </el-dialog>
  <el-dialog v-model="sourceOpen" title="比对来源维护" width="min(800px, 94vw)">
    <el-alert title="资产和综资的数据错误请先在原系统修改，再上传。综资更新接受所选机房的完整清单，保留现场结果；地点编码仅供历史空值补录。" type="info" :closable="false" />
    <el-table :data="selectedTasks" border class="source-table"><el-table-column prop="room_name" label="机房" min-width="180" /><el-table-column prop="room_code" label="现场地点编码" min-width="130" /><el-table-column label="操作" width="220"><template #default="{row}"><el-button v-if="!row.room_code" link type="primary" @click="fillLocation(row)">补地点编码</el-button><el-button link type="primary" @click="uploadFor(row)">更新综资基线</el-button></template></el-table-column></el-table>
    <p class="source-note">字段沿用任务模板，包含必填地点编码。上传资产信息表请进入“资产信息表”模块。</p>
    <input ref="resourceInput" type="file" accept=".xlsx" hidden @change="updateResources" />
  </el-dialog>
  <el-dialog v-model="aiHistoryOpen" title="历史 AI 辅助分析报告" width="min(1200px,96vw)">
    <div class="toolbar-actions"><el-checkbox v-model="aiHistoryCurrentOnly" :disabled="!selectedIds.length" @change="aiHistoryPage=1;loadAiHistory()">仅所选机房（含联合分析报告）</el-checkbox><el-button :loading="aiHistoryLoading" @click="loadAiHistory">刷新</el-button></div>
    <el-table v-loading="aiHistoryLoading" :data="aiHistory.items" border stripe :max-height="480" class="source-table" empty-text="当前范围暂无历史 AI 报告">
      <el-table-column prop="id" label="报告编号" width="95" />
      <el-table-column label="生成时间" width="160"><template #default="{row}">{{formatTime(row.created_at)}}</template></el-table-column>
      <el-table-column prop="analysis_scope" label="分析机房范围" min-width="260" show-overflow-tooltip />
      <el-table-column label="状态" width="90"><template #default="{row}"><el-tag :type="row.status==='success'?'success':'danger'">{{row.status==='success'?'成功':'失败'}}</el-tag></template></el-table-column>
      <el-table-column prop="model_name" label="分析模型" min-width="130" />
      <el-table-column label="生成人" width="110"><template #default="{row}">{{row.created_by_name || '未记录'}}</template></el-table-column>
      <el-table-column label="操作" width="140"><template #default="{row}"><el-button link type="primary" @click="viewAiReport(row.id)">查看</el-button><el-button link type="primary" @click="downloadAiReport(row.id)">下载</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-model:current-page="aiHistoryPage" :page-size="20" :total="aiHistory.total" layout="total, prev, pager, next" class="pagination" @current-change="loadAiHistory" />
  </el-dialog>
  <el-dialog v-model="aiOpen" :title="`AI 辅助分析报告 #${aiReport?.id || ''}`" width="min(1200px,94vw)">
    <template v-if="aiReport">
      <p class="source-note">生成时间：{{formatTime(aiReport.created_at)}} · 模型：{{aiReport.model_name}}<br />分析范围：{{aiReport.analysis_scope}}</p>
      <el-alert v-if="aiReport.status!=='success'" :title="aiReport.error_message || '本次分析失败，未生成分析结论'" type="error" :closable="false" class="notice" />
      <el-alert title="报告保留生成时的分析结果；最新问题和验收结论请以当前三方比对及管理员核实为准。" type="info" :closable="false" />
      <el-table :data="aiReport.analysis_items" border :max-height="520" class="source-table" empty-text="本次分析没有返回问题条目"><el-table-column v-for="column in aiColumns" :key="column" :prop="column" :label="column" min-width="160" /></el-table>
    </template>
    <template #footer><el-button @click="aiOpen=false">关闭</el-button><el-button v-if="aiReport" type="primary" @click="downloadAiReport(aiReport.id)">下载报告</el-button></template>
  </el-dialog>
  <el-dialog v-if="permissions.configureAi" v-model="aiSettingsOpen" title="AI 辅助设置" width="520px"><el-form label-width="100px"><el-form-item label="启用 AI"><el-switch v-model="agentEnabled" @change="toggleAgent" /></el-form-item><el-form-item label="模型">{{agentStatus?.model_name || '尚未配置'}}</el-form-item><el-form-item label="服务来源">{{agentStatus?.model_provider || '-'}}</el-form-item></el-form><p class="source-note">已统一加载三方比对业务规则。AI 仅辅助解释，型号判断和验收仍需管理员确认。</p><template #footer><el-button :loading="checking" :disabled="!agentEnabled" @click="checkAgent">检测模型</el-button><el-button type="primary" @click="aiSettingsOpen=false">完成</el-button></template></el-dialog>
  <ComparisonDecisionDialog v-model="decisionOpen" :group="activeGroup" @saved="loadComparison" />
  <RecordCorrectionDialog v-model="correctionOpen" :record="correctionRecord" @saved="loadComparison" />
</template>
<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, onMounted, ref} from 'vue';
import {DataAnalysis, Tickets} from '@element-plus/icons-vue';
import {ElMessage, ElMessageBox} from 'element-plus';
import {apiGet, apiPost, apiPatch, downloadBlob, getToken} from '../api';
import {saveAiCheckSession, setAiChatTaskIds} from '../aiSession';
import type {AiStatus, TaskRecord, AiReportSummary, AiSavedReport} from '../types';
import {formatTime} from '../utils';
import {permissions} from '../permissions';
import {sourceRecords} from "../threeWay";
import {comparisonIssues} from '../comparisonIssues';
import {comparisonTaskTime, queryCompletedRooms} from '../comparisonSelection';
import type {ComparisonGroup, ComparisonResult, ComparisonTask} from '../threeWay';
import PageHeader from '../components/PageHeader.vue';
import SectionTitle from '../components/SectionTitle.vue';
import ThreeWayTable from '../components/ThreeWayTable.vue';
import CabinetComparisonTable from '../components/CabinetComparisonTable.vue';
import ComparisonDecisionDialog from '../components/ComparisonDecisionDialog.vue';
import RecordCorrectionDialog from '../components/RecordCorrectionDialog.vue';
const tasks=ref<ComparisonTask[]>([]), selectedIds=ref<number[]>([]), result=ref<ComparisonResult|null>(null);
const roomQueryField=ref('all'), roomQueryText=ref(''), roomQueryLoading=ref(false), roomPickerOpen=ref(false);
const roomCandidates=ref<ComparisonTask[]>([]), roomDialogSelection=ref<ComparisonTask[]>([]);
const roomPickerTable=ref<{clearSelection():void;toggleRowSelection(row:ComparisonTask,selected:boolean):void}|null>(null);
let roomQuerySequence=0, comparisonSequence=0;
const loading=ref(false), keyword=ref(''), filter=ref('all'), page=ref(1), rackPage=ref(1), unlinkedPage=ref(1), entityKind=ref('devices');
const activeGroup=ref<ComparisonGroup|null>(null), decisionOpen=ref(false), detailOpen=ref(false), sourceOpen=ref(false), correctionOpen=ref(false), assetScopeOpen=ref(false);
const correctionRecord=ref<TaskRecord|null>(null), resourceInput=ref<HTMLInputElement|null>(null), uploadTask=ref<ComparisonTask|null>(null);
const aiSettingsOpen=ref(false),agentEnabled=ref(true),agentStatus=ref<AiStatus|null>(null);
const checking=ref(false), agentReady=ref(false), analyzing=ref(false), aiOpen=ref(false), aiReport=ref<AiSavedReport|null>(null);
const aiHistoryOpen=ref(false), aiHistoryLoading=ref(false), aiHistoryCurrentOnly=ref(true), aiHistoryPage=ref(1);
const aiHistory=ref<{items:AiReportSummary[];total:number}>({items:[],total:0});
const aiColumns=['问题类型','风险等级','涉及机房','涉及设备','判断依据','整改建议','优先级'];
const selectedTasks=computed(()=>tasks.value.filter(t=>selectedIds.value.includes(t.id)));
const mixedBatches=computed(()=>new Set(selectedTasks.value.map(t=>t.import_batch_id)).size>1);
const filtered=computed(()=>(result.value?.groups || []).filter(g=>{
  const q=keyword.value.trim().toLowerCase();
  const sources=[...['综资','现场','资产'].flatMap(system=>sourceRecords(g,system)),...Object.values(g.manual_decision?.values || {})];
  const text=[g.barcode,g.device_name,g.room_name,...sources.flatMap(r=>[r.barcode,r.name,r.location_code,r.rack_no])].join(' ').toLowerCase();
  return (!q || text.includes(q)) && (filter.value==='all' || filter.value==='issues' && !g.consistent || filter.value==='consistent' && g.consistent || filter.value==='pending' && hasPendingModel(g) || filter.value==='manual' && g.manual);
}));
const rackFiltered=computed(()=>(result.value?.rack_comparison || []).filter(row=>!keyword.value.trim() ||
  [row.room_name,row.resource_name,row.field_barcode,row.asset_barcodes].join(' ').toLowerCase().includes(keyword.value.trim().toLowerCase())));
const cabinetFiltered=computed(()=>(result.value?.cabinet_comparison || []).filter(row=>!keyword.value.trim() ||
  [row.room_name,row.resource_name,row.position,row.field_barcodes,row.asset_barcodes].join(' ').toLowerCase().includes(keyword.value.trim().toLowerCase())));
const rackPageRows=computed(()=>rackFiltered.value.slice((rackPage.value-1)*30,rackPage.value*30));
const unlinkedFiltered=computed(()=>(result.value?.unlinked_assets || []).filter(row=>!keyword.value.trim() ||
  [row.barcode,row.location_code,row.model].join(' ').toLowerCase().includes(keyword.value.trim().toLowerCase())));
const unlinkedPageRows=computed(()=>unlinkedFiltered.value.slice((unlinkedPage.value-1)*30,unlinkedPage.value*30));
const pageGroups=computed(()=>filtered.value.slice((page.value-1)*30,page.value*30));
const metrics=computed(()=>[{label:'比对机房',value:result.value?.summary.task_count || 0},{label:'设备比对条目',value:result.value?.summary.device_count || 0},{label:'三方一致',value:result.value?.summary.matched_count || 0},{label:'问题 / 待处理',value:result.value?.summary.issue_count || 0},{label:'型号待判断',value:(result.value?.groups || []).filter(hasPendingModel).length}]);
function showAssetCategory(kind:string,barcode:string) {entityKind.value=kind==='rack'?'racks':kind==='unlinked'?'unlinked':'devices';keyword.value=barcode;filter.value='all';page.value=1;rackPage.value=1;unlinkedPage.value=1;assetScopeOpen.value=false;}
function hasPendingModel(g:ComparisonGroup) {return g.field_model_status==='待判断' || g.asset_model_status==='待判断';}
function url(path='/reconciliation',ids=selectedIds.value) {return `${path}?${ids.map(id=>`task_ids=${id}`).join('&')}`;}
function clearScopeResult() {comparisonSequence+=1;loading.value=false;result.value=null;activeGroup.value=null;decisionOpen.value=false;detailOpen.value=false;assetScopeOpen.value=false;sourceOpen.value=false;correctionOpen.value=false;setAiChatTaskIds([]);}
async function queryRooms() {
  if(roomQueryLoading.value) return;
  if(roomQueryField.value!=='all' && !roomQueryText.value.trim()) {ElMessage.warning('请输入查询内容');return;}
  const sequence=++roomQuerySequence, field=roomQueryField.value, query=roomQueryText.value;
  roomQueryLoading.value=true;
  try {
    const fresh=await apiGet<ComparisonTask[]>('/summary');
    if(sequence!==roomQuerySequence) return;
    tasks.value=fresh;roomCandidates.value=queryCompletedRooms(fresh,field,query);roomDialogSelection.value=[];roomPickerOpen.value=true;
    await nextTick();
    if(sequence!==roomQuerySequence || !roomPickerOpen.value) return;
    roomPickerTable.value?.clearSelection();
    for(const task of roomCandidates.value) if(selectedIds.value.includes(task.id)) roomPickerTable.value?.toggleRowSelection(task,true);
  } finally {if(sequence===roomQuerySequence) roomQueryLoading.value=false;}
}
async function confirmRooms() {
  if(!roomDialogSelection.value.length) return;
  selectedIds.value=roomDialogSelection.value.map(t=>t.id);roomPickerOpen.value=false;clearScopeResult();
  page.value=1;rackPage.value=1;unlinkedPage.value=1;filter.value='all';keyword.value='';
  await loadComparison();
}
async function loadComparison() {
  if (!selectedIds.value.length) return;
  const sequence=++comparisonSequence, ids=[...selectedIds.value];
  loading.value=true;
  try {
    const fresh=await apiGet<ComparisonResult>(url('/reconciliation',ids),{timeout:60000});
    if(sequence!==comparisonSequence) return;
    result.value=fresh;setAiChatTaskIds(ids);page.value=Math.min(page.value,Math.max(1,Math.ceil(filtered.value.length/30)));rackPage.value=Math.min(rackPage.value,Math.max(1,Math.ceil(rackFiltered.value.length/30)));unlinkedPage.value=Math.min(unlinkedPage.value,Math.max(1,Math.ceil(unlinkedFiltered.value.length/30)));
  } finally {if(sequence===comparisonSequence) loading.value=false;}
}
function canCorrect(g:ComparisonGroup) {return [...sourceRecords(g,"现场"),...sourceRecords(g,"综资")].some(r=>r.record_id);}
function judge(g:ComparisonGroup) {activeGroup.value=g;decisionOpen.value=true;}
function detail(g:ComparisonGroup) {activeGroup.value=g;detailOpen.value=true;}
async function correct(g:ComparisonGroup) {
  const rows=[...sourceRecords(g,"现场"),...sourceRecords(g,"综资")].filter(r=>r.record_id);
  const unique=[...new Map(rows.map(r=>[r.record_id,r])).values()];
  if (!unique.length) return;
  let recordId=unique[0].record_id;
  if (unique.length>1) {
    try {const p=await ElMessageBox.prompt(unique.map(r=>`#${r.record_id} ${r.name}（${r.system}）`).join('；'),'选择要修正的设备记录',{inputPlaceholder:'输入记录编号',inputValidator:v=>unique.some(r=>String(r.record_id)===v?.trim()) || '请输入列表中的记录编号'});recordId=Number(p.value);} catch {return;}
  }
  const row=rows.find(r=>r.record_id===recordId)!;
  const report=await apiGet<{records:TaskRecord[]} | TaskRecord[]>(`/report/${row.task_id}`);
  correctionRecord.value=(Array.isArray(report)?report:report.records).find(r=>r.id===recordId) || null;
  if (correctionRecord.value?.scanned_at) correctionOpen.value=true; else ElMessage.warning('该设备没有现场反馈，请通过整改单让现场核实对应设备');
}
async function createCase(g:ComparisonGroup) {
  try {await ElMessageBox.confirm(g.problems.join('；'),'确认问题并建立整改单',{confirmButtonText:'建单',type:'warning'});} catch {return;}
  await apiPost('/reconciliation/adjustments',{task_ids:g.scope_task_ids,group_key:g.group_key,fingerprint:g.fingerprint});
  ElMessage.success('已建单，请进入整改闭环台账审核'); await loadComparison();
}
async function fillLocation(t:ComparisonTask) {
  let code=''; try {const p=await ElMessageBox.prompt('填写原任务清单中该机房的地点编码，编码按文本保存，保留前导零。','补录历史地点编码',{inputValidator:v=>!!v?.trim() || '地点编码不能为空'});code=p.value;} catch {return;}
  await apiPost(`/reconciliation/tasks/${t.id}/location`,{location_code:code}); tasks.value=await apiGet<ComparisonTask[]>('/summary'); await loadComparison();
}
function uploadFor(t:ComparisonTask) {uploadTask.value=t;resourceInput.value?.click();}
async function updateResources(event:Event) {
  const input=event.target as HTMLInputElement, file=input.files?.[0]; input.value='';
  if (!file || !uploadTask.value) return;
  try {await ElMessageBox.confirm(`用“${file.name}”更新 ${uploadTask.value.room_name} 的完整综资清单？请确认已在综资原系统修改。`,'更新综资基线',{confirmButtonText:'更新'});} catch {return;}
  const data=new FormData();data.append('file',file);
  await apiPost(`/reconciliation/tasks/${uploadTask.value.id}/resources`,data);ElMessage.success('基线已更新，现场结果已保留');await loadComparison();
}
async function exportComparison() {await downloadBlob(url('/reconciliation/export'),'三系统比对.xlsx',{timeout:60000});}
async function toggleAgent() {await apiPatch('/ai/status',{enabled:agentEnabled.value});if(!agentEnabled.value){agentReady.value=false;saveAiCheckSession(getToken(),{ok:false,message:'AI 已关闭',modelName:agentStatus.value?.model_name || ''});}}
async function checkAgent() {
  checking.value=true;try {const r=await apiPost<{ok:boolean;message:string;status:AiStatus}>('/ai/check');agentReady.value=r.ok;saveAiCheckSession(getToken(),{ok:r.ok,message:r.message,modelName:r.status.model_name || ''});ElMessage[r.ok?'success':'warning'](r.message);} finally {checking.value=false;}
}
async function openAiHistory() {
  aiHistoryPage.value=1; aiHistoryOpen.value=true; await loadAiHistory();
}
async function loadAiHistory() {
  const params=new URLSearchParams({page:String(aiHistoryPage.value),page_size:'20'});
  if(aiHistoryCurrentOnly.value) selectedIds.value.forEach(id=>params.append('task_ids',String(id)));
  aiHistoryLoading.value=true;
  try {aiHistory.value=await apiGet<{items:AiReportSummary[];total:number}>(`/ai/results?${params}`);} finally {aiHistoryLoading.value=false;}
}
async function viewAiReport(id:number) {
  aiReport.value=await apiGet<AiSavedReport>(`/ai/results/${id}`); aiOpen.value=true;
}
async function downloadAiReport(id:number) {await downloadBlob(`/ai/results/${id}/download`,`AI辅助分析报告_${id}.xlsx`);}
async function analyze() {
  if(mixedBatches.value) {ElMessage.warning('AI 辅助分析暂需同一批次，请缩小机房选择范围。');return;}
  const sequence=comparisonSequence, ids=[...selectedIds.value];
  analyzing.value=true;
  try {
    const report=await apiPost<AiSavedReport>('/ai/analyze',{task_ids:ids},{timeout:90000});
    if(sequence!==comparisonSequence) return;
    aiReport.value=report;
    aiOpen.value=true;
    if(aiReport.value.reused) ElMessage.info('已打开当前数据版本的已有报告');
    else if(aiReport.value.status!=='success') ElMessage.error(aiReport.value.error_message);
  } finally {analyzing.value=false;}
}
onMounted(async()=>{setAiChatTaskIds([]);if(permissions.value.configureAi){const status=await apiGet<AiStatus>('/ai/status');agentStatus.value=status;agentEnabled.value=status.enabled;}});
onBeforeUnmount(()=>{roomQuerySequence+=1;comparisonSequence+=1;setAiChatTaskIds([]);});
</script>
<style scoped>
.source-note {font-size:13px;color:var(--muted);line-height:1.8;margin:0 0 14px;overflow-wrap:anywhere;}
.notice {margin-bottom:12px;}
.room-query-toolbar {display:flex;flex-wrap:wrap;align-items:center;gap:14px 20px;}
.room-query-field {flex:0 1 285px;min-width:0;}
.room-query-text {flex:1 1 300px;min-width:0;}
.room-query-actions {margin-left:auto;}
.room-selection-summary {display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin:0 2px 16px;color:var(--muted);font-size:13px;}
.room-selection-summary strong {color:#365976;white-space:nowrap;}
.selected-room-names {flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.room-picker-footer {display:flex;align-items:center;justify-content:flex-end;gap:10px;}
.room-picker-footer span {margin-right:auto;color:var(--muted);}
.room-picker-footer .el-button+.el-button {margin-left:0;}
.asset-scope-note {padding:14px 16px;margin-bottom:16px;border:1px solid var(--line);border-radius:8px;font-size:13px;line-height:1.8;background:#f0f8ff;}
.asset-scope-heading {display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 16px;}
.asset-scope-heading strong {font-size:14px;}
.asset-scope-heading b,.asset-scope-counts b {color:var(--primary-dark);}
.asset-scope-counts {display:flex;flex-wrap:wrap;gap:8px 28px;margin-top:8px;}
.asset-scope-counts b {margin-left:6px;font-size:16px;}
.asset-scope-hint {margin:8px 0 0;color:var(--muted);}
.comparison-toolbar {display:flex;flex-direction:column;align-items:stretch;gap:14px;}
.comparison-filters {display:flex;flex-wrap:wrap;align-items:center;gap:14px 20px;}
.comparison-keyword {flex:1 1 360px;min-width:0;}
.comparison-result-filter {flex:0 1 320px;min-width:0;}
.comparison-actions {display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding-top:14px;border-top:1px solid var(--line);}
.comparison-ai-actions {display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-left:auto;}
.comparison-actions .el-button+.el-button {margin-left:0;}
.toolbar-actions {display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;}
.toolbar-actions .el-button+.el-button {margin-left:0;}
.action-stack {display:flex;flex-direction:column;align-items:flex-start;gap:8px;}
.action-stack .el-button+.el-button {margin-left:0;}
.pagination {justify-content:flex-end;margin-top:16px;}
.problem-line {border-bottom:1px solid var(--line);padding:12px 0;line-height:1.7;overflow-wrap:anywhere;}
.problem-evidence {font-size:13px;color:var(--muted);margin-top:3px;}
.source-table {margin-top:18px;}
.comparison-tabs {margin-top:8px;}
@media (max-width:820px) {
  .room-query-field,.room-query-text {flex-basis:100%;}
  .comparison-keyword,.comparison-result-filter {flex-basis:100%;}
  .comparison-ai-actions {margin-left:0;}
}
</style>
