// This file is referenced by createNextIntlPlugin in next.config.ts for module
// aliasing. With output: 'export' it is never called at request time — all i18n
// is handled client-side in ClientProviders. Keep in place so the plugin does
// not error during build.
import {getRequestConfig} from 'next-intl/server';
import {cookies} from 'next/headers';

export default getRequestConfig(async () => {
  const cookieStore = await cookies();
  const locale = cookieStore.get('NEXT_LOCALE')?.value || 'fr';
 
  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
    timeZone: 'UTC'
  };
});
