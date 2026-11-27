/**
 * The Zoho accounts hosts: where a Zoho consent URL may send the browser
 * (API-5 review, P3).
 *
 * The one list is `ACCOUNTS_HOSTS` in
 * `apps/services/gateway/gateway/crm_sources/zoho/auth.py`, made from
 * `ZOHO_DATA_CENTRES`. TypeScript cannot import Python, so this file mirrors
 * it. `tests/unit/test_api_connect_zoho_books.py` reads this file and fails
 * when the two lists differ.
 */
export const ZOHO_ACCOUNTS_HOSTS: readonly string[] = [
  "accounts.zoho.com",
  "accounts.zoho.eu",
  "accounts.zoho.in",
  "accounts.zoho.com.au",
  "accounts.zoho.jp",
  "accounts.zohocloud.ca",
  "accounts.zoho.sa",
  "accounts.zoho.uk",
];
