import './assets/styles.css';
import './components/animations.js';

document.addEventListener('DOMContentLoaded', () => {
  const loginBtn = document.getElementById('login-btn');
  if (loginBtn) {
    loginBtn.addEventListener('click', () => {
      window.location.href = 'http://localhost:8000/auth/login';
    });
  }
});
