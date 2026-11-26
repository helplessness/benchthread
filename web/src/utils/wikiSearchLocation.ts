// Match a rendered block to an actual line of the indexed passage. Never
// pick the first copy of an ambiguous quote or rely on old screen offsets.
export function wikiHitBlock(chunk: string, blocks: string[]): number {
  const normalize = (s: string) => s.replace(/\s+/g, ' ').trim();
  const texts = blocks.map(normalize);
  const lines = chunk.split('\n').map(line => normalize(line
    .replace(/^\s*(?:#{1,6}|>|[-*+] |\d+[.)] )\s*/, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/\*\*|__|`/g, '')))
    .filter(Boolean).sort((a, b) => b.length - a.length);
  for (const line of lines) {
    const indexes = texts.flatMap((text, i) => text === line || (line.length >= 12 && text.includes(line)) ? [i] : []);
    if (indexes.length === 1) return indexes[0];
  }
  return -1;
}
