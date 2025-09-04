// Login click
const loginBtn = document.getElementById('login-btn');
if (loginBtn) {
  loginBtn.addEventListener('click', () => {
    window.location.href = '/auth/login';
  });
}

/**
 * Subtle pointer-follow shadow/parallax for the button.
 * Works with mouse or touch; degrades gracefully with reduced motion.
 */
(function attachInteractiveShadow(){
  const btn = loginBtn;
  if (!btn) return;

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReduced) return; // keep it static

  btn.classList.add('shadow-follow');

  // Track pointer within the button and update a shadow offset
  const updateShadow = (clientX, clientY) => {
    const rect = btn.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const dx = (clientX - cx) / (rect.width / 2);  // -1..1
    const dy = (clientY - cy) / (rect.height / 2); // -1..1

    // scale offsets (smaller for subtlety)
    const ox = (dx * 12).toFixed(2);
    const oy = (dy * 12).toFixed(2);

    // Shadow that moves opposite the pointer to imply depth
    const dynamicShadow = `${-ox}px ${-oy}px 28px rgba(29,185,84,0.35)`;
    btn.style.setProperty('--btn-shadow', dynamicShadow);
  };

  const onPointerMove = (e) => {
    if (e.touches && e.touches[0]) {
      updateShadow(e.touches[0].clientX, e.touches[0].clientY);
    } else {
      updateShadow(e.clientX, e.clientY);
    }
  };

  const resetShadow = () => {
    btn.style.removeProperty('--btn-shadow');
  };

  btn.addEventListener('pointermove', onPointerMove);
  btn.addEventListener('pointerleave', resetShadow);
  btn.addEventListener('touchmove', onPointerMove, { passive: true });
  btn.addEventListener('touchend', resetShadow);

  // Press feedback
  btn.addEventListener('pointerdown', () => {
    btn.style.filter = 'brightness(.97)';
  });
  window.addEventListener('pointerup', () => {
    btn.style.filter = '';
  });
})();
