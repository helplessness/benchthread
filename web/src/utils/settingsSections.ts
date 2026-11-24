export type SettingsSection = 'account' | 'general' | 'notifications' | 'models' | 'collaboration' | 'structure' | 'knowledge' | 'mcp' | 'desktop' | 'feishu' | 'websearch' | 'internet-content';
export const settingsGroups = ['应用偏好', '任务与研究', '服务与集成'] as const;
const sections: { id: SettingsSection; label: string; detail: string; group: typeof settingsGroups[number] }[] = [
  { id: 'general', label: '常规', detail: '界面与交互', group: '应用偏好' },
  { id: 'account', label: '账号与安全', detail: '当前账号与登录密码', group: '应用偏好' },
  { id: 'notifications', label: '通知', detail: '任务完成提醒', group: '应用偏好' },
  { id: 'models', label: '模型与执行', detail: '模型供应商、推理、调度与验证', group: '任务与研究' },
  { id: 'collaboration', label: '协作与 Agent', detail: '办公室与调度', group: '任务与研究' },
  { id: 'structure', label: '三维结构', detail: 'Mol* 与 Agent 视图', group: '任务与研究' },
  { id: 'knowledge', label: '本体知识', detail: '工作区知识源', group: '任务与研究' },
  { id: 'mcp', label: 'MCP 服务', detail: '连接与远程工具', group: '服务与集成' },
  { id: 'desktop', label: 'Artifact 文件服务', detail: '远端服务与 MCP 绑定', group: '服务与集成' },
  { id: 'websearch', label: '网页搜索', detail: 'SearXNG 与 Brave Search', group: '服务与集成' },
  { id: 'internet-content', label: '互联网渠道', detail: '内容读取与渠道检查', group: '服务与集成' },
  { id: 'feishu', label: '飞书接入', detail: '机器人与任务通知', group: '服务与集成' },
];

export const availableSettingsSections = (hasAccount: boolean, hasWorkspace: boolean) => sections.filter(({id}) => (
  (id !== 'account' || hasAccount) && (id !== 'knowledge' || hasWorkspace)
));

export const isSettingsSection = (value: unknown): value is SettingsSection => sections.some(({id}) => id === value);
