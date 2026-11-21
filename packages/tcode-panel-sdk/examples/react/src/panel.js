import {getPanel} from '@tcode-ai/panel-sdk';
import {createMockPanel} from '@tcode-ai/panel-sdk/mock';

export const preview = import.meta.env.DEV && !window.tcodePanel;
export const panel = preview
  ? createMockPanel({grants: ['team.run', 'selection.share']})
  : getPanel();
