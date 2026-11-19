export function csvPreview(content: string) {
  const text = content.replace(/^\uFEFF/, '');
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  let limited = false;
  const pushField = () => {
    if (row.length < 100) row.push(field);
    else limited = true;
    field = '';
  };
  for (let i = 0; i < text.length; i++) {
    if (rows.length === 200) return { rows, limited: true };
    const char = text[i];
    if (quoted && char === '"' && text[i + 1] === '"') {
      field += '"';
      i++;
    } else if (char === '"' && (quoted || field === '')) {
      quoted = !quoted;
    } else if (!quoted && char === ',') {
      pushField();
    } else if (!quoted && (char === '\n' || char === '\r')) {
      if (char === '\r' && text[i + 1] === '\n') i++;
      pushField();
      rows.push(row);
      row = [];
    } else {
      field += char;
    }
  }
  if (field || row.length || (text.length && !/[\r\n]$/.test(text))) {
    pushField();
    rows.push(row);
  }
  return { rows, limited };
}
