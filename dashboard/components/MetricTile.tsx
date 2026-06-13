type MetricTileProps = {
  label: string;
  value: string;
  detail: string;
  tone?: "green" | "yellow" | "red" | "blue";
};

export function MetricTile({
  label,
  value,
  detail,
  tone = "blue"
}: MetricTileProps) {
  return (
    <section className="metricTile">
      <div className="metricLabel">{label}</div>
      <div className={`metricValue ${tone}`}>{value}</div>
      <div className="metricDetail">{detail}</div>
    </section>
  );
}

