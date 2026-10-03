import { Health, VenueStatus } from "../api";
import { Events } from "./Logs";
import { FundingMatrix, PriceMatrix, SpotList } from "./Markets";
import { CarryOpps, CrossOpps } from "./Opps";
import { Health as HealthPage, Summary } from "./Overview";
import { Planned } from "./Planned";
import { FeesSettings, General, Keys, VenueSettings } from "./Settings";
import { VenuePage } from "./Venue";

export type PageProps = { route: string; venues: VenueStatus[]; health: Health | null };

export function Page(p: PageProps) {
  const [s, i] = p.route.split("/");
  switch (s) {
    case "overview": return i === "health" ? <HealthPage {...p} /> : <Summary {...p} />;
    case "venues": return <VenuePage name={i} venues={p.venues} />;
    case "markets": return i === "prices" ? <PriceMatrix /> : i === "spot" ? <SpotList /> : <FundingMatrix />;
    case "opportunities": return i === "carry" ? <CarryOpps /> : <CrossOpps />;
    case "logs": return <Events />;
    case "settings":
      return i === "fees" ? <FeesSettings venues={p.venues} /> : i === "venues" ? <VenueSettings venues={p.venues} />
        : i === "keys" ? <Keys /> : <General />;
    default: return <Planned route={p.route} />;
  }
}
