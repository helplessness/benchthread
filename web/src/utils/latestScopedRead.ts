// A late success, failure or finally handler must never update a newer view.
export function latestScopedRead(scope: () => string) {
  let version = 0;
  return {
    begin() {
      const expected = ++version, owner = scope();
      return () => expected === version && owner === scope();
    },
    invalidate() { ++version; },
  };
}
