import React from "react";

export default function Modal({ plotUrl, onClose }) {
  if (!plotUrl) return null; // без этого картинка может не отрендериться

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>
          ✕
        </button>
        <img src={plotUrl} alt="График" />
      </div>
    </div>
  );
}