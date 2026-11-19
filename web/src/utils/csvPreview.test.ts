import { expect, it } from 'vitest';
import { csvPreview } from './csvPreview';

it('preserves quoted commas, escaped quotes, newlines and trailing empty cells', () => {
  expect(csvPreview('\uFEFFname,note,empty\r\n"a,b","line 1\r\nline ""2""",\r\n')).toEqual({
    rows: [['name', 'note', 'empty'], ['a,b', 'line 1\r\nline "2"', '']],
    limited: false,
  });
});

it('limits rendered rows and columns without miscounting quoted newlines', () => {
  const row = Array.from({ length: 101 }, () => '"a\nb"').join(',');
  const result = csvPreview(Array.from({ length: 201 }, () => row).join('\n'));
  expect(result.rows).toHaveLength(200);
  expect(result.rows[199]).toHaveLength(100);
  expect(result.limited).toBe(true);
});
