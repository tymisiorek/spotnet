export default function LoginPage() {
  const handleLogin = () =>
    (window.location.href = "http://127.0.0.1:8000/auth/login");

  return (
    <div style={{ padding: 20 }}>
      <h1>SpotNet</h1>
      <button onClick={handleLogin}>Login with Spotify</button>
    </div>
  );
}
