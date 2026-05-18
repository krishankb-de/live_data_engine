import { Link } from "react-router";

type FilterPill = { label: string; active?: boolean };

const FILTER_PILLS: FilterPill[] = [
  { label: "Beste Treffer", active: true },
  { label: "Bewertung" },
  { label: "Geöffnet" },
];

type Listing = {
  name: string;
  badge?: "SILBER PARTNER" | "TOP PARTNER";
  logo?: { src?: string; bg: string; label: string };
  rating: number;
  reviews: number;
  category: string;
  description?: string;
  address: string;
  hours: string;
  phone: string;
  hasEmail?: boolean;
  hasWebsite?: boolean;
};

const LISTINGS: Listing[] = [
  {
    name: "Gasthof Gundel",
    badge: "SILBER PARTNER",
    rating: 5.0,
    reviews: 1,
    category: "GASTSTÄTTEN UND RESTAURANTS",
    description:
      "Griechische Spezialitäten kombiniert mit klassischer deutscher Küche — herzlich willkommen im Gastha…",
    address: "Nördlinger Str. 14, 91126 Kammerstein  (Barthelmesaurach)",
    hours: "Geschlossen — Öffnet um 11:00",
    phone: "09178 15 03",
  },
  {
    name: 'DORNHEIM GmbH - Bootsverleih & Restaurant "Zur Gondel"',
    badge: "TOP PARTNER",
    logo: { bg: "bg-[#6b1f3a]", label: "ZUR GONDEL" },
    rating: 3.8,
    reviews: 5,
    category: "GASTSTÄTTEN UND RESTAURANTS",
    address: "Kaemmererufer 25, 22303 Hamburg  (Winterhude)",
    hours: "Geschlossen — Öffnet um 10:00",
    phone: "040 2 79 41 84",
    hasEmail: true,
    hasWebsite: true,
  },
  {
    name: "Lanfgasthof Wetzdorf",
    rating: 4.9,
    reviews: 7,
    category: "GASTSTÄTTEN UND RESTAURANTS",
    address: "Hauptstraße 12, 91126 Wetzdorf",
    hours: "Geöffnet — Schließt um 22:00",
    phone: "09178 22 14",
    hasEmail: true,
  },
];

function Stars({ rating }: { rating: number }) {
  const full = Math.floor(rating);
  const half = rating - full >= 0.5;
  return (
    <span aria-label={`${rating} von 5 Sternen`} className="text-[#f5b400] tracking-tight">
      {Array.from({ length: 5 }).map((_, i) => {
        if (i < full) return <span key={i}>★</span>;
        if (i === full && half) return <span key={i}>★</span>;
        return (
          <span key={i} className="text-[#f5b400]/40">
            ★
          </span>
        );
      })}
    </span>
  );
}

function ListingCard({ listing }: { listing: Listing }) {
  return (
    <article className="bg-white rounded-md px-8 py-7 shadow-[0_1px_2px_rgba(0,0,0,0.05)]">
      {listing.logo && (
        <div className={`${listing.logo.bg} text-white w-32 h-20 mb-5 flex items-center justify-center rounded-sm`}>
          <span className="text-xs font-semibold tracking-wide text-center leading-tight">
            {listing.logo.label}
          </span>
        </div>
      )}

      <div className="flex items-start justify-between gap-4">
        <h3 className="text-[22px] font-bold text-black leading-tight">{listing.name}</h3>
        {listing.badge && (
          <span className="text-[11px] font-semibold tracking-wider text-black/70 shrink-0 pt-1">
            {listing.badge}
          </span>
        )}
      </div>

      <div className="flex items-center gap-2 mt-2 text-sm">
        <span className="font-semibold">{listing.rating.toFixed(1).replace(".", ",")}</span>
        <Stars rating={listing.rating} />
        <span className="text-black">
          {listing.reviews} {listing.reviews === 1 ? "Bewertung" : "Bewertungen"}
        </span>
      </div>

      <p className="mt-1 text-[11px] font-semibold tracking-wider text-black/70">
        {listing.category}
      </p>

      {listing.description && (
        <>
          <hr className="my-5 border-black/10" />
          <p className="text-[15px] text-black leading-snug">{listing.description}</p>
        </>
      )}

      {(listing.hasEmail || listing.hasWebsite) && (
        <>
          <hr className="my-5 border-black/10" />
          <div className="flex items-center gap-3">
            {listing.hasEmail && (
              <button
                type="button"
                className="bg-[#ffcc00] text-black text-sm font-medium px-4 py-2 rounded-sm inline-flex items-center gap-2 hover:brightness-95"
              >
                <span aria-hidden="true">➤</span> E-Mail
              </button>
            )}
          </div>
        </>
      )}

      <hr className="my-5 border-black/10" />

      <ul className="flex flex-col gap-3 text-[15px]">
        <li className="flex items-start gap-3">
          <span className="text-[#f5b400] mt-0.5" aria-hidden="true">
            ⌖
          </span>
          <span>{listing.address}</span>
        </li>
        <li className="flex items-start gap-3">
          <span className="text-[#f5b400] mt-0.5" aria-hidden="true">
            ◷
          </span>
          <span className={listing.hours.startsWith("Geöffnet") ? "" : "text-[#c40000]"}>
            {listing.hours}
          </span>
        </li>
        <li className="flex items-start gap-3">
          <span className="text-[#f5b400] mt-0.5" aria-hidden="true">
            ☎
          </span>
          <span>{listing.phone}</span>
        </li>
        {listing.hasWebsite && (
          <li className="flex items-start gap-3">
            <span className="text-[#f5b400] mt-0.5" aria-hidden="true">
              ⌾
            </span>
            <a href="#" className="underline">
              Webseite ↗
            </a>
          </li>
        )}
      </ul>
    </article>
  );
}

function FeedbackCard() {
  return (
    <aside className="bg-white rounded-md p-7 text-center shadow-[0_1px_2px_rgba(0,0,0,0.05)]">
      <div className="mx-auto w-24 h-24 rounded-full bg-[#ffcc00] flex items-center justify-center text-4xl">
        <span aria-hidden="true">☺</span>
      </div>
      <h4 className="mt-5 text-xl font-bold">Ihre Meinung zählt!</h4>
      <p className="mt-3 text-[15px] text-black/80 leading-snug">
        Was gefällt Ihnen gut?
        <br />
        Was sollten wir verbessern?
      </p>
      <button
        type="button"
        className="mt-5 bg-[#ffcc00] text-black font-semibold text-[15px] px-5 py-3 rounded-sm hover:brightness-95"
      >
        Jetzt Feedback geben!
      </button>
    </aside>
  );
}

function SellwerkAd() {
  return (
    <aside className="bg-white rounded-md p-7 shadow-[0_1px_2px_rgba(0,0,0,0.05)]">
      <p className="text-[13px] font-semibold tracking-wider text-black/70">≡ SELLWERK</p>
      <p className="mt-4 text-[26px] font-extrabold leading-tight">
        In 2 Minuten
        <br />
        zur eigenen
        <br />
        Website —<br />
        kostenlos
        <br />
        mit KI!
      </p>
      <p className="mt-4 text-[15px] text-black/80">
        DSGVO-konform,
        <br />
        SEO-optimiert.
      </p>
      <div className="mt-4 bg-[#f4f4f5] rounded-sm px-3 py-2 text-xs text-black/60">
        ✨ Zaubern
      </div>
    </aside>
  );
}

export function LandingPage() {
  return (
    <div className="min-h-screen bg-[#ffcc00]">
      <header className="max-w-[1400px] mx-auto px-10 pt-8">
        <div className="flex items-center justify-between">
          <h1 className="text-[40px] font-extrabold tracking-tight text-black">Gelbe Seiten</h1>

          <nav className="flex items-center gap-8 text-[17px] text-black">
            <a href="#" className="hover:underline">
              Suchen
            </a>
            <a href="#" className="hover:underline">
              Service
            </a>
            <a href="#" className="hover:underline">
              Ratgeber
            </a>
            <Link to="/dashboard" className="hover:underline font-semibold">
              Dashboard
            </Link>
            <button
              type="button"
              className="bg-black text-white px-5 py-3 rounded-sm font-medium text-[15px] hover:bg-black/85"
            >
              Meine Firma eintragen
            </button>
            <button
              type="button"
              aria-label="Barrierefreiheit"
              className="w-10 h-10 rounded-full border-2 border-black flex items-center justify-center text-lg"
            >
              <span aria-hidden="true">⚛</span>
            </button>
          </nav>
        </div>

        {/* Search bar */}
        <div className="mt-10 flex bg-white rounded-sm overflow-hidden shadow-[0_1px_2px_rgba(0,0,0,0.05)]">
          <input
            type="text"
            defaultValue="restaurants"
            className="flex-1 px-6 py-5 text-[17px] text-black outline-none"
          />
          <div className="w-px bg-black/10" />
          <input
            type="text"
            placeholder="Wo"
            className="flex-1 px-6 py-5 text-[17px] text-black outline-none placeholder:text-black/50"
          />
          <button
            type="button"
            className="bg-black text-white px-10 text-[17px] font-medium hover:bg-black/85"
          >
            Finden
          </button>
        </div>
      </header>

      <main className="max-w-[1400px] mx-auto px-10 mt-10 pb-20">
        <h2 className="text-[22px] font-bold">Restaurants (94949 Treffer)</h2>

        <div className="mt-5 flex items-center gap-3">
          {FILTER_PILLS.map((pill) => (
            <button
              key={pill.label}
              type="button"
              className={[
                "px-5 py-2 rounded-full text-[15px] font-medium border",
                pill.active
                  ? "bg-black text-white border-black"
                  : "bg-transparent text-black border-black/80 hover:bg-black/5",
              ].join(" ")}
            >
              {pill.label}
            </button>
          ))}
        </div>

        <div className="mt-6 grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6 items-start">
          <div className="flex flex-col gap-6">
            {LISTINGS.map((l) => (
              <ListingCard key={l.name} listing={l} />
            ))}
          </div>
          <div className="flex flex-col gap-6">
            <FeedbackCard />
            <SellwerkAd />
          </div>
        </div>
      </main>
    </div>
  );
}
