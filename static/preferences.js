/* Apply the saved theme before the stylesheet and first paint. */
(() => {
  const key = 'laundry_preferences';
  const themes = ['system', 'light', 'dark'];
  let saved = {};
  try {
    const value = JSON.parse(window.localStorage.getItem(key) || '{}');
    if (value && typeof value === 'object' && !Array.isArray(value)) saved = value;
  } catch (_) {}
  const preferences = {
    theme: themes.includes(saved.theme) ? saved.theme : 'system',
    buildingId: typeof saved.buildingId === 'string' ? saved.buildingId : '',
    minutes: Number.isInteger(saved.minutes) && saved.minutes >= 1 && saved.minutes <= 30 ? saved.minutes : 5,
  };
  const media = typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function save() {
    try { window.localStorage.setItem(key, JSON.stringify(preferences)); } catch (_) {}
  }

  function applyTheme() {
    const theme = preferences.theme === 'system' ? (media && media.matches ? 'dark' : 'light') : preferences.theme;
    document.documentElement.dataset.theme = theme;
    document.documentElement.dataset.themeMode = preferences.theme;
    const color = document.getElementById('theme-color');
    if (color) color.setAttribute('content', theme === 'dark' ? '#111316' : '#f5f7f3');
    window.dispatchEvent(new window.CustomEvent('laundrythemechange', { detail: { mode: preferences.theme, theme } }));
  }

  window.LaundryPreferences = {
    getTheme: () => preferences.theme,
    setTheme(mode) {
      preferences.theme = themes.includes(mode) ? mode : 'system'; save(); applyTheme();
    },
    getBuilding(buildings) {
      const selected = buildings.find(building => String(building.id) === preferences.buildingId);
      const id = String((selected || buildings[0]).id);
      if (preferences.buildingId && preferences.buildingId !== id) {
        preferences.buildingId = id; save();
      }
      return id;
    },
    setBuilding(id) { preferences.buildingId = String(id); save(); },
    getMinutes: () => preferences.minutes,
    setMinutes(minutes) {
      if (!Number.isInteger(minutes) || minutes < 1 || minutes > 30) return;
      preferences.minutes = minutes; save();
    },
  };
  if (media) {
    if (typeof media.addEventListener === 'function') media.addEventListener('change', applyTheme);
    else if (typeof media.addListener === 'function') media.addListener(applyTheme);
  }
  applyTheme();
})();
