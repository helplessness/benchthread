import type {PanelCapability, PanelTurn, TCodePanel} from './index.js';

export interface MockOptions {
  grants?: PanelCapability[];
  delayMs?: number;
  respond?: (turn: PanelTurn) => string | Promise<string>;
  onShare?: (selection: Parameters<TCodePanel['share']>[0]) => void;
}
export function createMockPanel(options?: MockOptions): TCodePanel;
