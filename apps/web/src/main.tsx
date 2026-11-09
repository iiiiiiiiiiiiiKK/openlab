import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { OverviewPage } from "@/pages/Overview";
import { ProjectsPage } from "@/pages/Projects";
import { ProjectWorkspacePage } from "@/pages/ProjectWorkspace";
import { RunningPage } from "@/pages/Running";
import { ExplorerPage } from "@/pages/Explorer";
import { AssetsPage } from "@/pages/Assets";
import { ReportsPage } from "@/pages/Reports";
import { ProvidersPage } from "@/pages/Providers";
import { SettingsPage } from "@/pages/Settings";
import { ToastProvider } from "@/components/ui/toast";
import { I18nProvider } from "@/lib/i18n";
import "./index.css";

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "projects", element: <ProjectsPage /> },
      { path: "projects/:projectId", element: <ProjectWorkspacePage /> },
      { path: "running", element: <RunningPage /> },
      { path: "explorer", element: <ExplorerPage /> },
      { path: "assets", element: <AssetsPage /> },
      { path: "reports", element: <ReportsPage /> },
      { path: "providers", element: <ProvidersPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
]);

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("root element missing");

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <I18nProvider>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </I18nProvider>
  </React.StrictMode>,
);
