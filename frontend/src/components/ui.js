function qs(id) { return document.getElementById(id); }

const drawer = qs('drawer');
const drawerToggle = qs('drawerToggle');
const drawerClose = qs('drawerClose');
const backdrop = qs('drawerBackdrop');

const statsToggle = document.querySelector('.card__toggle');
const statsBody = qs('statsBody');
const statNodes = qs('statNodes');
const statEdges = qs('statEdges');
const statTopK = qs('statTopK');

const uiWrapper = qs('ui');
const proceedBtn = qs('proceed-btn'); 

function openDrawer() {
  drawer.classList.add('drawer--open');
  backdrop.hidden = false;
  drawer.setAttribute('aria-hidden', 'false');
}
function closeDrawer() {
  drawer.classList.remove('drawer--open');
  backdrop.hidden = true;
  drawer.setAttribute('aria-hidden', 'true');
}

drawerToggle?.addEventListener('click', openDrawer);
drawerClose?.addEventListener('click', closeDrawer);
backdrop?.addEventListener('click', closeDrawer);

if (statsToggle && statsBody) {
  statsToggle.addEventListener('click', () => {
    const expanded = statsToggle.getAttribute('aria-expanded') === 'true';
    statsToggle.setAttribute('aria-expanded', String(!expanded));
    if (expanded) {
      statsBody.hidden = true;
    } else {
      statsBody.hidden = false;
      // When expanding, refresh once
      refreshStats();
    }
  });
}

// Stats refresh
function refreshStats() {
  const fg = window.theGraph;
  if (!fg || !fg.graphData) return;

  const { nodes, links, TOP_K } = safeGraphData(fg);
  statNodes.textContent = numberFmt(nodes?.length);
  statEdges.textContent = numberFmt(links?.length);
  statTopK.textContent  = (typeof TOP_K === 'number') ? TOP_K : '-';
}

function safeGraphData(fg) {
  try {
    const data = fg.graphData();
    const TOP_K = (typeof fg.TOP_K === 'number') ? fg.TOP_K : 25;
    return { ...data, TOP_K };
  } catch {
    return { nodes: [], links: [], TOP_K: 25 };
  }
}

function numberFmt(x) {
  if (typeof x !== 'number') return '—';
  return x.toLocaleString();
}

if (proceedBtn) {
  proceedBtn.addEventListener('click', () => {
    // Give the overlay time to fade out and the UI to show then compute stats
    let attempts = 0;
    const maxAttempts = 10;
    const interval = setInterval(() => {
      attempts += 1;
      refreshStats();
      if (attempts >= maxAttempts) clearInterval(interval);
      if (statNodes.textContent !== '-' && statNodes.textContent !== '-') {
        clearInterval(interval);
      }
    }, 300);
  });
}

