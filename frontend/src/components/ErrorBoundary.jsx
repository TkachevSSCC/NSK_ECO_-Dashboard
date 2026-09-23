import { Component } from "react";

/**
 * Глобальная граница ошибок: вместо «белого экрана» при сбое рендера
 * показывает сообщение об ошибке и кнопку перезагрузки интерфейса.
 */
export default class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("ErrorBoundary:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            padding: 24,
            fontFamily: "system-ui, sans-serif",
            maxWidth: 720,
            margin: "40px auto",
          }}
        >
          <h2 style={{ color: "#ef4444" }}>Ошибка интерфейса</h2>
          <p style={{ color: "#555" }}>
            При рендере произошла ошибка. Ниже — текст ошибки, его можно
            скопировать и показать разработчику.
          </p>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              background: "#1e1e1e",
              color: "#f5f5f5",
              padding: 12,
              borderRadius: 8,
              fontSize: 13,
            }}
          >
            {String(this.state.error?.stack || this.state.error)}
          </pre>
          <button
            onClick={() => {
              this.setState({ error: null });
            }}
          >
            Попробовать снова
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}