// A section that failed to load is not an empty list. Keep its label alongside
// the successful values so independent sections can still be displayed.
export async function readResearchSections<const T extends readonly Promise<unknown>[]>(
  requests: T,
  labels: NoInfer<{ [K in keyof T]: string }>,
) {
  const results = await Promise.allSettled(requests);
  return { results, unavailable: results.flatMap((result, i) => result.status === 'rejected' ? [labels[i]] : []) };
}

export async function confirmResearchReproduction(
  ask: () => Promise<boolean>, stillCurrent: () => boolean, submit: () => Promise<void>,
) {
  if (await ask() && stillCurrent()) await submit();
}
