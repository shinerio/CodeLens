import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { MemoryRouter } from "react-router-dom";

import { I18nProvider, type Locale } from "../shared/i18n/i18n";

export function TestProviders({
  children,
  initialEntries = ["/"],
  locale = "en",
}: {
  children: ReactNode;
  initialEntries?: string[];
  locale?: Locale;
}) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  );
  return (
    <I18nProvider locale={locale}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
      </QueryClientProvider>
    </I18nProvider>
  );
}
