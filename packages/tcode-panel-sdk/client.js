/** Access the bridge injected into this panel by the current TCode session. */
export function getPanel() {
  const panel = globalThis.window?.tcodePanel;
  if (!panel) throw new Error('请在 TCode 侧栏中打开，或在开发预览中使用 createMockPanel()');
  return panel;
}
