// Three regions: sessions on the left, the conversation in the centre, the inspector on the right. On a narrow
// screen the left region and the inspector become drawers over the centre, which stays the page.

import { Menu } from "lucide-react";
import { useState } from "react";
import { Inspector } from "./components/inspector/Inspector";
import { SessionPanel } from "./components/SessionPanel";
import { StoreProvider, useStore } from "./state/store";
import { ArchitectureView } from "./views/ArchitectureView";
import { SystemView } from "./views/SystemView";
import { WorkspaceView } from "./views/WorkspaceView";

function Layout() {
  const { view, panel } = useStore();
  const [nav, setNav] = useState(false);
  const inspecting = view === "workspace" && panel != null;
  const menu = (
    <button className="icon-btn bare menu-btn" onClick={() => setNav(true)} aria-label="Open sessions and views">
      <Menu aria-hidden />
    </button>
  );
  return (
    <div className="app" data-nav={nav ? "open" : undefined} data-inspector={inspecting ? "open" : undefined}>
      <div className="scrim nav-scrim" onClick={() => setNav(false)} aria-hidden />
      <SessionPanel onNavigate={() => setNav(false)} />
      <main className="main">
        {view === "workspace" ? <WorkspaceView menu={menu} /> : view === "architecture" ? <ArchitectureView menu={menu} /> : <SystemView menu={menu} />}
      </main>
      {inspecting ? <Inspector /> : null}
    </div>
  );
}

export function App() {
  return (
    <StoreProvider>
      <Layout />
    </StoreProvider>
  );
}
