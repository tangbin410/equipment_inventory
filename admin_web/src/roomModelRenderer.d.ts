import type { RoomModelData } from './roomModel'

export type RoomModelView = 'overview' | 'top' | 'front' | 'back'

/** Native Canvas renderer. Internal panels are illustrative, not surveyed U/port data. */
export default class RoomRenderer {
  constructor(canvas: HTMLCanvasElement, options?: { onSelect?: (id: string) => void })
  setData(data: RoomModelData): void
  setView(view: RoomModelView): void
  setRow(row: string | null): void
  select(id: string | null): void
  setShowLabels(show: boolean): void
  setShowDevices(show: boolean): void
  reset(): void
  destroy(): void
}
