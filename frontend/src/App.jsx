import { Routes, Route } from "react-router-dom";
import LoginPage from "./pages/LoginPage";
import GraphPage from "./pages/GraphPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LoginPage />} />
      <Route path="/hello" element={<GraphPage />} />
    </Routes>
  );
}
