import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";

import { App } from "./app/App";
import { CatalogPreviewPage } from "./features/catalog/CatalogPreviewPage";
import { NewReviewPage } from "./features/reviews/NewReviewPage";
import { FixPreviewPage } from "./features/reviews/FixPreviewPage";
import { RunListPage } from "./features/reviews/RunListPage";
import { ReviewRunPage } from "./features/reviews/ReviewRunPage";
import { SettingsPage } from "./features/settings/SettingsPage";
import { I18nProvider } from "./shared/i18n/i18n";

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

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Navigate replace to="/runs" /> },
      { path: "reviews/new", element: <NewReviewPage /> },
      { path: "runs", element: <RunListPage /> },
      { path: "reviews/:taskId", element: <ReviewRunPage /> },
      { path: "runs/:taskId", element: <ReviewRunPage /> },
      { path: "fix/:fixId", element: <FixPreviewPage /> },
      { path: "agents", element: <CatalogPreviewPage kind="agents" /> },
      { path: "capabilities", element: <CatalogPreviewPage kind="capabilities" /> },
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
