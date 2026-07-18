import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";

import { App } from "./app/App";
import { NewReviewPage } from "./features/reviews/NewReviewPage";
import { ReviewRunPage } from "./features/reviews/ReviewRunPage";
import { SettingsPage } from "./features/settings/SettingsPage";
import { I18nProvider, useI18n } from "./shared/i18n/i18n";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
    },
    mutations: {
      retry: false,
    },
  },
});

function ReviewAgentsPage() {
  const { t } = useI18n();
  return <h1>{t("nav.reviewAgents")}</h1>;
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Navigate replace to="/reviews/new" /> },
      { path: "reviews/new", element: <NewReviewPage /> },
      { path: "reviews/:taskId", element: <ReviewRunPage /> },
      { path: "runs/:taskId", element: <ReviewRunPage /> },
      { path: "agents", element: <ReviewAgentsPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);

const rootElement = document.getElementById("root");

if (rootElement === null) {
  throw new Error("CodeLens root element was not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <I18nProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </I18nProvider>
  </StrictMode>,
);
