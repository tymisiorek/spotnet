import { Routes, Route } from "react-router-dom"

function LoginPage() {
  const handleLogin = () => {
    // kicks off the OAuth redirect on your backend
    window.location.href = "http://127.0.0.1:8000/auth/login"
  }

  return (
    <div style={{ padding: 20 }}>
      <h1>SpotNet</h1>
      <button onClick={handleLogin}>
        Login with Spotify
      </button>
    </div>
  )
}

function HelloPage() {
  return (
    <div style={{ padding: 20 }}>
      <h1>Hello World</h1>
      <p>You’re now “logged in.”</p>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />
      <Route path="/hello" element={<HelloPage />} />
    </Routes>
  )
}
