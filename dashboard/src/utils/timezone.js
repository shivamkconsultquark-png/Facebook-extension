// Harness stubs for the main app's utils/timezone.js.
export const toZonedDate = (date, locale = 'en-US', opts = {}) => date.toLocaleDateString(locale, opts);
export const toZonedTime = (date) => date.toLocaleString();
