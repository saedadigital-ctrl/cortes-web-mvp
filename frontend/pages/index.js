import { useState } from "react";

export default function Home() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleTestApi = async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch("http://localhost:8000/status");

      if (!response.ok) {
        throw new Error(`Erro na resposta da API: ${response.status}`);
      }

      const data = await response.json();
      setStatus(JSON.stringify(data));
    } catch (err) {
      setError(err.message);
      setStatus(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main style={styles.container}>
      <h1 style={styles.title}>Cortes Web MVP</h1>
      <p style={styles.description}>
        Clique em "Testar API" para verificar o status do backend.
      </p>
      <div style={styles.controls}>
        <input
          type="text"
          placeholder="Digite algo"
          style={styles.input}
          disabled={loading}
        />
        <button onClick={handleTestApi} style={styles.button} disabled={loading}>
          {loading ? "Testando..." : "Testar API"}
        </button>
      </div>
      {status && (
        <div style={styles.result}>Resposta da API: {status}</div>
      )}
      {error && <div style={styles.error}>Erro: {error}</div>}
    </main>
  );
}

const styles = {
  container: {
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#f5f5f5",
    fontFamily: "Arial, sans-serif",
    padding: "2rem",
  },
  title: {
    fontSize: "2.5rem",
    marginBottom: "1rem",
  },
  description: {
    fontSize: "1.125rem",
    marginBottom: "2rem",
    textAlign: "center",
    maxWidth: "480px",
  },
  controls: {
    display: "flex",
    gap: "1rem",
    marginBottom: "1.5rem",
    width: "100%",
    maxWidth: "480px",
  },
  input: {
    flex: 1,
    padding: "0.75rem",
    fontSize: "1rem",
    borderRadius: "0.5rem",
    border: "1px solid #ccc",
  },
  button: {
    padding: "0.75rem 1.5rem",
    fontSize: "1rem",
    borderRadius: "0.5rem",
    border: "none",
    backgroundColor: "#2563eb",
    color: "#fff",
    cursor: "pointer",
  },
  result: {
    backgroundColor: "#dcfce7",
    color: "#166534",
    padding: "1rem",
    borderRadius: "0.5rem",
  },
  error: {
    backgroundColor: "#fee2e2",
    color: "#991b1b",
    padding: "1rem",
    borderRadius: "0.5rem",
  },
};
