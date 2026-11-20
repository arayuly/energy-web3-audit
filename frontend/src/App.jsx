import { useEffect, useState, useCallback } from "react";
import { ethers } from "ethers";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from "recharts";

const API_BASE = "http://127.0.0.1:8000";
const LOCAL_RPC = "http://127.0.0.1:8545";

const AUDIT_ABI = [
  "function getDeviceReadings(address deviceAddr) view returns (tuple(uint64 timestamp, uint64 energyWh, uint32 powerW, bytes32 ipfsCidHash)[])",
  "function penaltyScore(address consumer) view returns (uint256)",
];

const ERC20_ABI = [
  "function balanceOf(address owner) view returns (uint256)",
  "function symbol() view returns (string)",
];

function shortAddr(addr) {
  if (!addr) return "-";
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function clusterBadge(name) {
  if (!name) return "badge-blue";
  if (name.includes("Энергоэффект")) return "badge-green";
  if (name.includes("Неэффект") || name.includes("Аномал")) return "badge-red";
  return "badge-blue";
}

export default function App() {
  const [tab, setTab] = useState("overview");
  const [health, setHealth] = useState(null);
  const [devices, setDevices] = useState([]);
  const [analytics, setAnalytics] = useState(null);
  const [anomalies, setAnomalies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Web3 состояние (ethers.js)
  const [blockNumber, setBlockNumber] = useState(null);
  const [walletAddr, setWalletAddr] = useState("");
  const [walletEcot, setWalletEcot] = useState(null);

  // Audit Explorer
  const [selectedDevice, setSelectedDevice] = useState("");
  const [auditResult, setAuditResult] = useState(null);
  const [directOnchain, setDirectOnchain] = useState(null);
  const [directLatencyMs, setDirectLatencyMs] = useState(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [hRes, dRes, aRes, anRes] = await Promise.all([
        fetch(`${API_BASE}/health`),
        fetch(`${API_BASE}/api/devices`),
        fetch(`${API_BASE}/api/analytics/overview`),
        fetch(`${API_BASE}/api/anomalies?limit=100`),
      ]);
      if (!hRes.ok) throw new Error("Backend недоступен на http://127.0.0.1:8000");

      const hData = await hRes.json();
      const dData = await dRes.json();
      const aData = await aRes.json();
      const anData = await anRes.json();

      setHealth(hData);
      setDevices(dData);
      setAnalytics(aData);
      setAnomalies(anData);

      if (!selectedDevice && dData.length > 0) {
        setSelectedDevice(dData[0].device_address);
      }

      // Прямой запрос номера блока через ethers.js к локальной ноде
      try {
        const provider = new ethers.JsonRpcProvider(LOCAL_RPC);
        const bn = await provider.getBlockNumber();
        setBlockNumber(bn);
      } catch {
        setBlockNumber(null);
      }
    } catch (err) {
      setError(err.message || "Ошибка загрузки данных");
    } finally {
      setLoading(false);
    }
  }, [selectedDevice]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Подключение MetaMask через ethers.js v6
  async function connectWallet() {
    if (!window.ethereum) {
      alert("MetaMask не обнаружен. Установите расширение MetaMask или работайте в режиме локального RPC.");
      return;
    }
    try {
      const browserProvider = new ethers.BrowserProvider(window.ethereum);
      const signer = await browserProvider.getSigner();
      const address = await signer.getAddress();
      setWalletAddr(address);

      if (health?.contracts?.ecoToken) {
        const token = new ethers.Contract(health.contracts.ecoToken, ERC20_ABI, browserProvider);
        const bal = await token.balanceOf(address);
        setWalletEcot(Number(ethers.formatUnits(bal, 18)).toFixed(2));
      }
    } catch (err) {
      alert(`Ошибка подключения кошелька: ${err.message}`);
    }
  }

  // Независимый Web3-аудит выбранного устройства
  async function runAudit(deviceAddr) {
    if (!deviceAddr) return;
    try {
      const apiRes = await fetch(`${API_BASE}/api/audit/${deviceAddr}`);
      const apiData = await apiRes.json();
      setAuditResult(apiData);

      // Прямое чтение смарт-контракта из браузера через ethers.js (без посредника-бэкенда)
      if (health?.contracts?.energyAuditSystem) {
        const t0 = performance.now();
        const provider = new ethers.JsonRpcProvider(LOCAL_RPC);
        const contract = new ethers.Contract(
          health.contracts.energyAuditSystem,
          AUDIT_ABI,
          provider
        );
        const rows = await contract.getDeviceReadings(deviceAddr);
        const dt = (performance.now() - t0).toFixed(2);
        setDirectLatencyMs(dt);
        setDirectOnchain(
          rows.map((r) => ({
            timestamp: Number(r.timestamp),
            energyWh: Number(r.energyWh),
            powerW: Number(r.powerW),
            ipfsCidHash: r.ipfsCidHash,
          }))
        );
      }
    } catch (err) {
      alert(`Ошибка аудита: ${err.message}`);
    }
  }

  const totalTokens = devices.reduce((s, d) => s + Number(d.ecot_balance || 0), 0);
  const totalCerts = devices.reduce((s, d) => s + Number(d.certificates_count || 0), 0);

  const clusterByDevice = Object.fromEntries(
    (analytics?.clusters || []).map((c) => [c.device_address.toLowerCase(), c])
  );
  const recByDevice = Object.fromEntries(
    (analytics?.recommendations || []).map((r) => [r.device_address.toLowerCase(), r.recommendation])
  );

  // Сортировка рейтинга по балансу ECOT и энергоэффективности
  const sortedRating = [...devices].sort(
    (a, b) => (b.ecot_balance || 0) - (a.ecot_balance || 0) || (a.penalty_score || 0) - (b.penalty_score || 0)
  );

  return (
    <div className="container">
      <header className="header">
        <div>
          <h1>Urban Energy Web3 Audit Dashboard</h1>
          <div className="subtitle">
            Мониторинг и блокчейн-аудит энергопотребления городов (TRL 4–5)
          </div>
        </div>
        <div className="header-actions">
          <span className="status-pill">
            Chain ID: {health?.chain_id ?? "—"} | Блок: {blockNumber ?? "—"}
          </span>
          <button className="btn btn-outline" onClick={loadData}>
            Обновить данные
          </button>
          <button className="btn btn-green" onClick={connectWallet}>
            {walletAddr
              ? `${shortAddr(walletAddr)} (${walletEcot ?? "0"} ECOT)`
              : "Подключить MetaMask"}
          </button>
        </div>
      </header>

      {error && (
        <div className="card" style={{ borderColor: "var(--danger)", marginBottom: 16 }}>
          Ошибка: {error}
        </div>
      )}

      {/* KPI Карточки */}
      <section className="kpi-grid">
        <div className="card">
          <div className="kpi-label">Объектов в сети</div>
          <div className="kpi-value">{devices.length}</div>
          <div className="kpi-sub">Записей on-chain: {analytics?.summary?.total_readings ?? 0}</div>
        </div>
        <div className="card">
          <div className="kpi-label">Потребление / CO₂</div>
          <div className="kpi-value">{analytics?.summary?.total_kwh ?? 0} kWh</div>
          <div className="kpi-sub">{analytics?.summary?.total_co2_kg ?? 0} кг CO₂-экв.</div>
        </div>
        <div className="card">
          <div className="kpi-label">Средний газ записи</div>
          <div className="kpi-value">{analytics?.summary?.avg_gas_used ?? 0}</div>
          <div className="kpi-sub">Лимит ТЗ: &lt; 150 000 gas</div>
        </div>
        <div className="card">
          <div className="kpi-label">Начислено стимулов</div>
          <div className="kpi-value">{totalTokens.toFixed(1)} ECOT</div>
          <div className="kpi-sub">NFT-сертификатов: {totalCerts} шт.</div>
        </div>
        <div className="card">
          <div className="kpi-label">Алерты аномалий</div>
          <div className="kpi-value">{anomalies.length}</div>
          <div className="kpi-sub">ML-выбросов: {analytics?.ml_anomalies?.length ?? 0}</div>
        </div>
      </section>

      {/* Вкладки */}
      <nav className="tabs">
        <button className={`tab ${tab === "overview" ? "active" : ""}`} onClick={() => setTab("overview")}>
          Обзор и кластеры
        </button>
        <button className={`tab ${tab === "rating" ? "active" : ""}`} onClick={() => setTab("rating")}>
          Рейтинг, токены и рекомендации
        </button>
        <button className={`tab ${tab === "anomalies" ? "active" : ""}`} onClick={() => setTab("anomalies")}>
          Аномалии ({anomalies.length})
        </button>
        <button className={`tab ${tab === "audit" ? "active" : ""}`} onClick={() => setTab("audit")}>
          Web3 Audit Explorer
        </button>
      </nav>

      {loading && !analytics ? (
        <div className="card">Загрузка данных...</div>
      ) : (
        <>
          {/* Вкладка 1: Обзор и кластеры */}
          {tab === "overview" && (
            <>
              <div className="grid-2">
                <div className="card">
                  <h2>Потребление (kWh) и CO₂ (кг) по типам зданий</h2>
                  <div style={{ width: "100%", height: 280 }}>
                    <ResponsiveContainer>
                      <BarChart data={analytics?.by_building_type || []}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                        <XAxis dataKey="name" stroke="#9ca3af" fontSize={12} />
                        <YAxis stroke="#9ca3af" fontSize={12} />
                        <Tooltip
                          contentStyle={{ background: "#0f172a", border: "1px solid #334155" }}
                        />
                        <Legend />
                        <Bar dataKey="total_kwh" name="Потребление (kWh)" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                        <Bar dataKey="co2_kg" name="CO₂ (кг)" fill="#10b981" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="card">
                  <h2>Профиль нагрузки по объектам (% от базы)</h2>
                  <div style={{ width: "100%", height: 280 }}>
                    <ResponsiveContainer>
                      <BarChart data={(analytics?.clusters || []).slice(0, 12)}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                        <XAxis dataKey="label" stroke="#9ca3af" fontSize={11} angle={-20} textAnchor="end" height={60} />
                        <YAxis stroke="#9ca3af" fontSize={12} />
                        <Tooltip
                          contentStyle={{ background: "#0f172a", border: "1px solid #334155" }}
                        />
                        <Bar dataKey="utilization_pct" name="% от базовой линии" fill="#f59e0b" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </div>

              <div className="card">
                <h2>Кластеризация K-means по паттернам потребления</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Объект</th>
                        <th>Тип</th>
                        <th>Кластер (K-means)</th>
                        <th>Факт / База (kWh)</th>
                        <th>Использование</th>
                        <th>Пик-фактор</th>
                        <th>CO₂ (кг)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(analytics?.clusters || []).map((c) => (
                        <tr key={c.device_address}>
                          <td>{c.label || shortAddr(c.device_address)}</td>
                          <td>{c.building_name}</td>
                          <td>
                            <span className={`badge ${clusterBadge(c.cluster_name)}`}>
                              {c.cluster_name}
                            </span>
                          </td>
                          <td>
                            {c.total_kwh} / {c.baseline_kwh}
                          </td>
                          <td>{c.utilization_pct}%</td>
                          <td>{c.peak_factor}x</td>
                          <td>{c.co2_kg}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}

          {/* Вкладка 2: Рейтинг, токены и рекомендации */}
          {tab === "rating" && (
            <div className="card">
              <h2>Рейтинг энергоэффективности, токен-баланс (ERC-20 / ERC-721) и рекомендации</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Объект</th>
                      <th>Потребление</th>
                      <th>ECOT (ERC-20)</th>
                      <th>NFT (ERC-721)</th>
                      <th>Штраф</th>
                      <th>Рекомендация по экономии</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedRating.map((d, idx) => {
                      const cl = clusterByDevice[d.device_address.toLowerCase()];
                      const rec = recByDevice[d.device_address.toLowerCase()] || "—";
                      return (
                        <tr key={d.device_address}>
                          <td>{idx + 1}</td>
                          <td>
                            <div><strong>{d.label || shortAddr(d.device_address)}</strong></div>
                            <div className="mono" style={{ color: "var(--muted)" }}>
                              {shortAddr(d.device_address)} · {d.building_name}
                            </div>
                          </td>
                          <td>
                            {d.total_kwh} kWh
                            {cl && (
                              <div style={{ fontSize: 11, color: "var(--muted)" }}>
                                {cl.utilization_pct}% от базы
                              </div>
                            )}
                          </td>
                          <td>
                            <span className="badge badge-green">{d.ecot_balance} ECOT</span>
                          </td>
                          <td>{d.certificates_count} шт.</td>
                          <td>
                            {d.penalty_score > 0 ? (
                              <span className="badge badge-red">+{d.penalty_score}</span>
                            ) : (
                              "0"
                            )}
                          </td>
                          <td style={{ maxWidth: 360 }}>{rec}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Вкладка 3: Аномалии */}
          {tab === "anomalies" && (
            <div className="grid-2">
              <div className="card">
                <h2>Пороговые алерты (пики &gt;150%, утечки &gt;5%)</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Объект</th>
                        <th>Тип</th>
                        <th>Описание</th>
                      </tr>
                    </thead>
                    <tbody>
                      {anomalies.map((a) => (
                        <tr key={a.id}>
                          <td>{a.label || shortAddr(a.device_address)}</td>
                          <td>
                            <span
                              className={`badge ${
                                a.severity === "HIGH" ? "badge-red" : "badge-warn"
                              }`}
                            >
                              {a.anomaly_type}
                            </span>
                          </td>
                          <td>{a.details}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="card">
                <h2>ML-детекция аномалий (Isolation Forest)</h2>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Объект</th>
                        <th>Энергия / Мощность</th>
                        <th>Откл. от шага</th>
                        <th>ML Score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(analytics?.ml_anomalies || []).map((m, idx) => (
                        <tr key={`${m.device_address}-${m.timestamp}-${idx}`}>
                          <td>{m.label || shortAddr(m.device_address)}</td>
                          <td>
                            {m.energy_wh} Wh / {m.power_w} W
                          </td>
                          <td>
                            E: {m.energy_ratio}x | P: {m.power_ratio}x
                          </td>
                          <td className="mono">{m.anomaly_score}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}

          {/* Вкладка 4: Web3 Audit Explorer */}
          {tab === "audit" && (
            <div className="card">
              <h2>Независимый блокчейн-аудит показаний (On-chain via ethers.js vs Off-chain IPFS)</h2>
              <div className="audit-bar">
                <select
                  value={selectedDevice}
                  onChange={(e) => setSelectedDevice(e.target.value)}
                >
                  {devices.map((d) => (
                    <option key={d.device_address} value={d.device_address}>
                      {d.label || "Устройство"} — {shortAddr(d.device_address)}
                    </option>
                  ))}
                </select>
                <button className="btn" onClick={() => runAudit(selectedDevice)}>
                  Проверить аудит-трассу
                </button>
              </div>

              {auditResult && (
                <>
                  <div className="kpi-grid" style={{ marginTop: 10 }}>
                    <div className="card">
                      <div className="kpi-label">Целостность данных</div>
                      <div className="kpi-value" style={{ fontSize: 18 }}>
                        {auditResult.integrity_matched ? "Подтверждена (100%)" : "Расхождение!"}
                      </div>
                      <div className="kpi-sub">
                        On-chain: {auditResult.onchain_count} | Off-chain: {auditResult.offchain_count}
                      </div>
                    </div>
                    <div className="card">
                      <div className="kpi-label">Задержка аудита (API)</div>
                      <div className="kpi-value" style={{ fontSize: 18 }}>
                        {auditResult.audit_latency_ms} мс
                      </div>
                      <div className="kpi-sub">Цель ТЗ: &lt; 10 000 мс</div>
                    </div>
                    <div className="card">
                      <div className="kpi-label">Прямой RPC-запрос (ethers.js)</div>
                      <div className="kpi-value" style={{ fontSize: 18 }}>
                        {directLatencyMs ? `${directLatencyMs} мс` : "—"}
                      </div>
                      <div className="kpi-sub">
                        Записей из контракта: {directOnchain?.length ?? 0}
                      </div>
                    </div>
                  </div>

                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Timestamp</th>
                          <th>Энергия (Wh)</th>
                          <th>Мощность (W)</th>
                          <th>IPFS CID (Off-chain)</th>
                          <th>Tx Hash (On-chain)</th>
                          <th>Gas</th>
                        </tr>
                      </thead>
                      <tbody>
                        {auditResult.offchain_index.map((row) => (
                          <tr key={row.id}>
                            <td className="mono">{row.reading_timestamp}</td>
                            <td>{row.energy_wh}</td>
                            <td>{row.power_w}</td>
                            <td className="mono">
                              <a
                                href={`http://127.0.0.1:8080/ipfs/${row.ipfs_cid}`}
                                target="_blank"
                                rel="noreferrer"
                                style={{ color: "#60a5fa" }}
                              >
                                {row.ipfs_cid.slice(0, 16)}...
                              </a>
                            </td>
                            <td className="mono">{shortAddr(row.tx_hash)}</td>
                            <td>{row.gas_used}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}