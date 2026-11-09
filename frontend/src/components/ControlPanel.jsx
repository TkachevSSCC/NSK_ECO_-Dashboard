export default function ControlPanel({ option, setOption, onGenerate }) {
  return (
    <div
      style={{
        position: "absolute",
        top: 20,
        right: 20,
        zIndex: 1000,
        background: "white",
        padding: "10px",
        borderRadius: "5px",
      }}
    >
      <label htmlFor="plot-select">Выберите расписание: </label>
      <select
        id="plot-select"
        value={option}
        onChange={(e) => setOption(e.target.value)}
      >
        <option value="1">Time Zone</option>
        <option value="2">Cluster</option>
        <option value="3">Cluster with cluster heads</option>
        <option value="4">Cluster with battery life based cluster heads</option>
      </select>
      <button style={{ marginLeft: "10px" }} onClick={onGenerate}>
        Построить
      </button>
    </div>
  );
}
