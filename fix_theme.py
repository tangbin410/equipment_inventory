"""修改：淡蓝色主题 + 上传页文字整合"""

from pathlib import Path
import ast

p = Path("equipment_inventory/pc_admin/admin.py")
c = p.read_text("utf-8")

# 1. 侧边栏淡蓝背景
c = c.replace(
    "background: #f0f5ff; display: flex; flex-direction: column; height: 100vh; position: sticky; top: 0; border-right: 1px solid #d6e4ff;",
    "background: #f0f5ff; display: flex; flex-direction: column; height: 100vh; position: sticky; top: 0; border-right: 1px solid #d6e4ff;"
)
c = c.replace(
    "background: #fff; display: flex; flex-direction: column; height: 100vh; position: sticky; top: 0; border-right: 1px solid #e8e8e8;",
    "background: #f0f5ff; display: flex; flex-direction: column; height: 100vh; position: sticky; top: 0; border-right: 1px solid #d6e4ff;"
)

# 侧边栏分割线
c = c.replace(
    ".sidebar-logo { padding: 28px 24px 24px; border-bottom: 1px solid #e8e8e8; }",
    ".sidebar-logo { padding: 28px 24px 24px; border-bottom: 1px solid #d6e4ff; }"
)

# 导航 hover 颜色
c = c.replace(
    "color: #1677ff !important; background: #bae0ff !important",
    "color: #1677ff !important; background: #bae0ff !important"
)
c = c.replace(
    "color: #1677ff !important; background: #e6f4ff !important",
    "color: #1677ff !important; background: #bae0ff !important"
)

# 主内容区
c = c.replace(
    "background: #f7faff; padding: 28px 32px;",
    "background: #f7faff; padding: 28px 32px;"
)
c = c.replace(
    "background: #f5f5f5; padding: 28px 32px;",
    "background: #f7faff; padding: 28px 32px;"
)

# 边框颜色
c = c.replace("border: 1px solid #e8e8e8;", "border: 1px solid #d6e4ff;")
c = c.replace("border-bottom: 1px solid #e8e8e8; margin-bottom: 24px;", "border-bottom: 1px solid #d6e4ff; margin-bottom: 24px;")

print("1. 淡蓝色主题已应用")

# 2. 上传页说明文字移到标题下方
old = 'st.markdown(\'<div class="card"><div class="card-body"><span style="color:#666;font-size:14px;line-height:1.8;">'
new = "st.markdown('<p style=\"color:#666;font-size:14px;margin:-4px 0 20px 0;line-height:1.8;\">"

if old in c:
    c = c.replace(old, new)
    c = c.replace("</span></div></div>", "</p>")
    print("2. 说明文字已移至标题下方")
else:
    print("2. 未匹配到说明文字")

p.write_text(c, "utf-8")

try:
    ast.parse(c)
    print("admin.py 语法正确")
except SyntaxError as e:
    print(f"语法错误: {e}")

print()
print("重启 streamlit 查看效果")
print('streamlit run pc_admin/admin.py --server.address 0.0.0.0')
