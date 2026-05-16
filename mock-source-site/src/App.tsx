import { useState, useEffect } from "react";

interface BusinessData {
  name: string;
  phone: string;
  opening_hours: string;
  address: string;
}

const INITIAL: BusinessData = {
  name: "Nila Kitchen",
  phone: "+49 30 1234567",
  opening_hours: "Mon–Sun 11:00–22:00",
  address: "Kantstraße 42, 10625 Berlin",
};

export default function App() {
  const [biz, setBiz] = useState<BusinessData>(INITIAL);
  const [editPhone, setEditPhone] = useState(INITIAL.phone);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    fetch("/business.json")
      .then((r) => r.json())
      .then((data: BusinessData) => {
        setBiz(data);
        setEditPhone(data.phone);
      })
      .catch(() => {});
  }, []);

  async function handleSave() {
    setSaving(true);
    setSaved(false);
    await fetch("/update-phone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone: editPhone }),
    });
    setBiz((b) => ({ ...b, phone: editPhone }));
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  }

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", color: "#111" }}>
      {/* Admin strip */}
      <div style={{ background: "#1a1a1a", color: "#fff", padding: "8px 24px", display: "flex", alignItems: "center", gap: "12px", fontSize: "13px" }}>
        <span style={{ color: "#888" }}>Admin · Telefonnummer:</span>
        <input
          value={editPhone}
          onChange={(e) => setEditPhone(e.target.value)}
          style={{ background: "#333", color: "#fff", border: "1px solid #555", borderRadius: "4px", padding: "4px 8px", width: "200px" }}
        />
        <button
          onClick={handleSave}
          disabled={saving}
          style={{ background: "#FFCC00", color: "#000", border: "none", borderRadius: "4px", padding: "4px 14px", cursor: "pointer", fontWeight: "600" }}
        >
          {saving ? "…" : saved ? "✓ Gespeichert" : "Speichern"}
        </button>
      </div>

      {/* Hero */}
      <div style={{ background: "#1a1a1a", color: "#fff", padding: "80px 24px", textAlign: "center" }}>
        <p style={{ color: "#FFCC00", fontSize: "0.85rem", fontWeight: "600", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: "16px" }}>
          Südindisches Restaurant · Berlin
        </p>
        <h1 style={{ fontSize: "clamp(2rem, 5vw, 3.5rem)", fontWeight: "800", lineHeight: 1.2, marginBottom: "16px" }}>
          Das Beste Südindische<br />Restaurant In Berlin
        </h1>
        <p style={{ color: "#aaa", fontSize: "1.1rem", marginBottom: "40px" }}>
          Authentische südindische Küche im Herzen Berlins
        </p>
        <div style={{ display: "flex", justifyContent: "center", gap: "40px", flexWrap: "wrap", fontSize: "1rem", color: "#ddd" }}>
          <span>📍 {biz.address}</span>
          <span data-field="phone">📞 {biz.phone}</span>
          <span data-field="opening_hours">🕐 {biz.opening_hours}</span>
        </div>
      </div>

      {/* Welcome */}
      <div style={{ padding: "64px 24px", maxWidth: "800px", margin: "0 auto" }}>
        <h2 style={{ fontSize: "2rem", fontWeight: "700", marginBottom: "16px" }}>Willkommen bei Nila</h2>
        <p style={{ color: "#555", lineHeight: 1.8, marginBottom: "16px" }}>
          Seit über einem Jahrzehnt begeistert das Nila Kitchen die Berliner Gastronomie-Szene mit authentischen Gerichten aus Südindien.
          Unsere Köche stammen direkt aus Tamil Nadu und Kerala und bringen echte Rezepte mit, die von Generation zu Generation weitergegeben wurden.
        </p>
        <p style={{ color: "#555", lineHeight: 1.8 }}>
          Ob klassisches Masala Dosa, würziges Chettinad-Curry oder hausgemachte Chutneys — bei uns erleben Sie die volle Vielfalt der südindischen Küche.
          Vegetarische und vegane Optionen sind bei uns selbstverständlich.
        </p>
      </div>

      {/* Divider */}
      <hr style={{ border: "none", borderTop: "1px solid #eee", margin: "0 24px" }} />

      {/* Opening hours highlight */}
      <div style={{ padding: "48px 24px", textAlign: "center", background: "#fffdf5" }}>
        <h3 style={{ fontSize: "1.25rem", fontWeight: "700", marginBottom: "8px" }}>Öffnungszeiten</h3>
        <p data-field="opening_hours" style={{ fontSize: "1.1rem", color: "#444" }}>{biz.opening_hours}</p>
        <p style={{ color: "#888", fontSize: "0.9rem", marginTop: "8px" }}>Küche bis 21:30 Uhr · Reservierung empfohlen</p>
      </div>

      {/* Footer */}
      <div style={{ background: "#1a1a1a", color: "#fff", padding: "48px 24px" }}>
        <div style={{ maxWidth: "800px", margin: "0 auto", display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "32px" }}>
          <div>
            <p style={{ fontWeight: "700", marginBottom: "8px", color: "#FFCC00" }}>Nila Kitchen</p>
            <p style={{ color: "#aaa", fontSize: "0.9rem" }}>{biz.address}</p>
          </div>
          <div>
            <p style={{ fontWeight: "700", marginBottom: "8px" }}>Kontakt</p>
            <p data-field="phone" style={{ color: "#aaa", fontSize: "0.9rem" }}>{biz.phone}</p>
            <p style={{ color: "#aaa", fontSize: "0.9rem", marginTop: "4px" }}>info@nilakitchen.de</p>
          </div>
          <div>
            <p style={{ fontWeight: "700", marginBottom: "8px" }}>Öffnungszeiten</p>
            <p data-field="opening_hours" style={{ color: "#aaa", fontSize: "0.9rem" }}>{biz.opening_hours}</p>
          </div>
        </div>
        <p style={{ textAlign: "center", color: "#555", fontSize: "0.8rem", marginTop: "32px" }}>
          © 2026 Nila Kitchen · Alle Rechte vorbehalten
        </p>
      </div>
    </div>
  );
}
