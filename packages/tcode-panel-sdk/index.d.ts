export type PanelCapability = 'document.read' | 'team.run' | 'selection.share';
export type PanelRun = {
  id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
  question: string;
  document_name: string;
  result?: string | null;
  error?: string | null;
  source_run?: string;
  item_id?: string;
  selection?: string;
  artifact?: string | null;
};
export interface PanelTurn {
  request_id: string;
  question: string;
  document_name?: string;
  pdf?: string;
  source_run?: string;
  item_id?: string;
  selection?: string;
}
export interface TCodePanel {
  history(): Promise<{runs:PanelRun[]; grants:PanelCapability[]}>;
  submit(turn:PanelTurn): Promise<PanelRun>;
  share(selection:{run:string; item_id:string; text:string}): Promise<boolean>;
  cancel(run:string): Promise<PanelRun>;
}
declare global {
  interface Window { tcodePanel:TCodePanel }
}
export function getPanel(): TCodePanel;
