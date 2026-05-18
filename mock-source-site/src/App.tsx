import { useState, useEffect } from "react";

interface BusinessData {
  name: string;
  phone: string;
  opening_hours: string;
  address: string;
}

const INITIAL: BusinessData = {
  name: "Attorneyster Law Firm",
  phone: "+1 911-987-654-321",
  opening_hours: "Mon–Fri 09:00–18:00",
  address: "350 Fifth Avenue, Suite 4100, New York, NY 10118",
};

const PRACTICE_AREAS = [
  { title: "Business Law", blurb: "Corporate formations, contracts, and commercial dispute resolution for businesses of all sizes." },
  { title: "Construction Law", blurb: "Protecting contractors, owners, and developers through every phase of construction projects." },
  { title: "Car Accident", blurb: "Aggressive representation to ensure you receive full compensation after an automobile accident." },
  { title: "Wrongful Death", blurb: "Compassionate counsel for families pursuing justice after a preventable loss of life." },
  { title: "Criminal Law", blurb: "Strategic defense strategies from misdemeanors to complex federal criminal charges." },
  { title: "Family Law", blurb: "Guiding clients through divorce, custody, and family matters with clarity and care." },
];

export default function App() {
  const [biz, setBiz] = useState<BusinessData>(INITIAL);
  const [editPhone, setEditPhone] = useState(INITIAL.phone);
  const [editing, setEditing] = useState(false);
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
    setTimeout(() => {
      setSaved(false);
      setEditing(false);
    }, 1500);
  }

  function handleCancel() {
    setEditPhone(biz.phone);
    setEditing(false);
    setSaved(false);
  }

  return (
    <div>
      {/* TopBar */}
      <header className="topbar">
        <span className="topbar__wordmark">Attorneyster</span>

        <nav aria-label="Main navigation">
          <ul className="topbar__nav">
            <li><a href="#">Home</a></li>
            <li><a href="#">About Us</a></li>
            <li><a href="#">Pages</a></li>
            <li><a href="#">Contact Us</a></li>
          </ul>
        </nav>

        <div className="topbar__contact">
          <div className="topbar__phone-row">
            <span className="topbar__phone-label">Call Us On:</span>
            {editing ? (
              <div className="phone-edit-row">
                <input
                  aria-label="Edit phone number"
                  className="phone-input"
                  value={editPhone}
                  onChange={(e) => setEditPhone(e.target.value)}
                  autoFocus
                />
                <button
                  className="btn-save"
                  onClick={handleSave}
                  disabled={saving}
                >
                  {saving ? "…" : saved ? "✓ Saved" : "Save"}
                </button>
                <button className="btn-cancel" onClick={handleCancel}>Cancel</button>
              </div>
            ) : (
              <>
                <span data-field="phone" className="topbar__phone-number">{biz.phone}</span>
                <button
                  aria-label="Edit phone number"
                  className="btn-pencil"
                  onClick={() => { setEditing(true); setEditPhone(biz.phone); }}
                >
                  ✎
                </button>
              </>
            )}
          </div>
          <span className="topbar__email">Email Us On: info@attorneyster.com</span>
        </div>
      </header>

      {/* Header CTA strip */}
      <div className="cta-strip">
        <button className="btn-gold">Book A Consultation</button>
      </div>

      {/* Hero */}
      <section className="hero">
        <p className="hero__eyebrow">Certified Law Professionals</p>
        <h1 className="hero__h1">
          We're a Group of Certified<br />Law Professionals
        </h1>
        <p className="hero__sub">
          We have helped countless maritime workers and their families go up against the
          largest offshore companies and win.
        </p>
        <button className="btn-gold">Get In Touch</button>
      </section>

      {/* Welcome */}
      <section className="welcome">
        <div className="welcome__inner">
          <h2 className="welcome__h2">
            Welcome To Attorney Law – Lawyer &amp; Law Firm Company
          </h2>
          <p className="welcome__p">
            For over two decades, Attorneyster Law Firm has stood as a steadfast advocate for
            maritime workers and offshore injury victims across the United States. Our seasoned
            team of litigation specialists has secured multi-million-dollar verdicts against
            some of the industry's largest corporations, delivering justice where it matters
            most. Whether you face a Jones Act claim, an unseaworthiness action, or a complex
            offshore liability dispute, we combine meticulous case preparation with
            aggressive courtroom advocacy to protect your rights — and your future.
          </p>
        </div>
      </section>

      {/* Practice Areas */}
      <section className="practice">
        <div className="practice__inner">
          <h2 className="practice__heading">Explore Our Practice Areas</h2>
          <div className="practice__grid">
            {PRACTICE_AREAS.map((area) => (
              <div key={area.title} className="practice__card">
                <p className="practice__card-title">{area.title}</p>
                <p className="practice__card-blurb">{area.blurb}</p>
                <a href="#" className="practice__card-link">View More &rsaquo;</a>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Footer / Contact strip */}
      <footer className="footer">
        <div className="footer__grid">
          <div>
            <p className="footer__brand">ATTORNEYSTER</p>
            <p className="footer__copy">
              Providing trusted legal counsel to clients across New York and nationwide.
            </p>
            <p className="footer__copy" style={{ marginTop: "8px" }}>
              &copy; 2026 Attorneyster Law Firm. All rights reserved.
            </p>
          </div>
          <div>
            <p className="footer__col-title">Contact</p>
            <p className="footer__col-text" data-field="address">{biz.address}</p>
            <p data-field="phone" className="footer__col-text" style={{ marginTop: "8px" }}>{biz.phone}</p>
            <p className="footer__col-text" style={{ marginTop: "4px" }}>info@attorneyster.com</p>
          </div>
          <div>
            <p className="footer__col-title">Office Hours</p>
            <p data-field="opening_hours" className="footer__col-text">{biz.opening_hours}</p>
          </div>
        </div>
        <div className="footer__bottom">
          &copy; 2026 Attorneyster Law Firm &middot; All Rights Reserved
        </div>
      </footer>
    </div>
  );
}
