/** Load every interface weight/script before a screen announces readiness. */
export async function prepareInterfaceFonts(screen: HTMLElement, additionalText = '') {
  const fonts = screen.ownerDocument.fonts;
  if (!fonts) return;
  const style = getComputedStyle(screen);
  // Native language choices include Latin, Cyrillic and Kazakh text. Include API
  // names too, so their fonts do not first load after the screen becomes ready.
  const text = `${screen.textContent ?? ''}${additionalText}`;
  await Promise.all(
    [
      ['--font-sans', '--t-body-weight'],
      ['--font-sans', '--t-label-weight'],
      ['--font-sans', '--t-body-strong-weight'],
      ['--font-display', '--t-heading-weight'],
      ['--font-mono', '--t-mono-weight'],
    ].map(([familyToken, weightToken]) => {
      const family = style.getPropertyValue(familyToken).trim();
      const weight = style.getPropertyValue(weightToken).trim();
      return family && weight ? fonts.load(`${weight} 1em ${family}`, text) : Promise.resolve([]);
    }),
  );
  // Read ready after explicit loads, including their final layout/fallback work.
  await fonts.ready;
}
