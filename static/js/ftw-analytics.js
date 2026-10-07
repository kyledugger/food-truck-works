(() => {
  const root = document.querySelector('[data-ftw-analytics]');
  if (!root || !['foodtruckworks.com', 'www.foodtruckworks.com'].includes(location.hostname)) return;
  const pages = ['/', '/teaser', '/launch-updates', '/launch-updates/thanks'];
  if (!pages.includes(location.pathname)) return;
  const url = new URL(location.origin + location.pathname);
  const incoming = new URLSearchParams(location.search);
  // Campaign names must be public labels, never names, addresses or identifiers.
  for (const key of ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term']) {
    const value = incoming.get(key);
    if (value && /^[a-zA-Z0-9_-]{1,100}$/.test(value)) url.searchParams.set(key, value);
  }
  window.dataLayer = window.dataLayer || [];
  window.gtag = function () { window.dataLayer.push(arguments); };
  const fields = {
    page_location: url.href,
    page_referrer: '',
    page_title: 'Food Truck Works teaser',
    site_area: 'marketing',
    audience_type: root.dataset.audience || 'visitor'
  };
  try { if (document.referrer) fields.page_referrer = new URL(document.referrer).origin + '/'; } catch (_) {}
  window.gtag('js', new Date());
  window.gtag('config', 'G-SSFHN44M6R', {
    ...fields, send_page_view: false,
    allow_google_signals: false, allow_ad_personalization_signals: false
  });
  window.gtag('event', 'page_view', fields);
  const event = root.dataset.event;
  if (['launch_signup_requested', 'launch_signup_confirmed'].includes(event)) {
    window.gtag('event', event, fields);
  }
  const script = document.createElement('script');
  script.async = true;
  script.src = 'https://www.googletagmanager.com/gtag/js?id=G-SSFHN44M6R';
  document.head.appendChild(script);
})();
