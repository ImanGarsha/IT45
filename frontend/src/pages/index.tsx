import { Health, VenueStatus } from "../api";
import { Events } from "./Logs";
import { FundingMatrix, PriceMatrix, SpotList } from "./Markets";
import { CarryOpps, CrossOpps } from "./Opps";
import { Health as HealthPage, Summary } from "./Overview";
import { FeesSettings, General, VenueSettings } from "./Settings";
import { BrakesPage, HistoryPage, KeysPage, KillPage, LimitsPage, OpenOrdersPage, PositionsPage, StrategyPage } from "./Trading";
import { TicketPage } from "./Ticket";
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
        : i === "keys" ? <KeysPage /> : <General />;
    case "strategies": return i === "spot-carry" ? <StrategyPage key="sc" name="spot_carry" /> : <StrategyPage key="fa" name="funding_arb" />;
    case "orders": return i === "new" ? <TicketPage /> : i === "open" ? <OpenOrdersPage /> : i === "history" ? <HistoryPage /> : <PositionsPage />;
    case "risk": return i === "brakes" ? <BrakesPage /> : i === "kill" ? <KillPage /> : <LimitsPage />;
    default: return <Summary {...p} />;
  }
}
