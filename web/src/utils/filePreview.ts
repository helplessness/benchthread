const extension = (path: string | null | undefined) => {
  const cleanPath = String(path || '').trim().toLowerCase().split(/[?#]/, 1)[0];
  return cleanPath.includes('.') ? cleanPath.split('.').pop() || '' : '';
};

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'svg', 'bmp']);
/** Presentation only: never pass this label back to the file APIs. */
export function displayWorkspacePath(path: string, desktop = false) {
  const root = /^@roots\/([^/]+)(?:\/(.*))?$/.exec(path);
  if (desktop && root) return [root[1] === 'primary' ? '主项目' : '项目文件夹', root[2]].filter(Boolean).join('/');
  return path.startsWith('/') ? path : '/workspace/' + path;
}
const OFFICE_EXTENSIONS = new Set(['docx', 'xlsx', 'pptx']);

export const isImageFile = (path: string | null | undefined) => IMAGE_EXTENSIONS.has(extension(path));
export const isPdfFile = (path: string | null | undefined) => extension(path) === 'pdf';
export const isHtmlFile = (path: string | null | undefined) => ['html', 'htm'].includes(extension(path));
export const isOfficeFile = (path: string | null | undefined) => OFFICE_EXTENSIONS.has(extension(path));

