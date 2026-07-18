import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";

import { App } from "./app/App";
import { NewReviewPage } from "./features/reviews/NewReviewPage";
import { ReviewRunPage } from "./features/reviews/ReviewRunPage";

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

function RunsPage() {
  return <h1>Runs</h1>;
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <NewReviewPage /> },
      { path: "runs", element: <RunsPage /> },
      { path: "runs/:taskId", element: <ReviewRunPage /> },
    ],
  },
]);

const rootElement = document.getElementById("root");

if (rootElement === null) {
  throw new Error("CodeLens root element was not found");
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
