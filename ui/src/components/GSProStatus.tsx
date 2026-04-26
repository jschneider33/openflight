import './GSProStatus.css';
import type { GSProStatus as GSProStatusType } from '../hooks/useSocket';

interface Props {
  status: GSProStatusType | null;
}

export function GSProStatus({ status }: Props) {
  if (status === null) {
    // GSPro not enabled — hide the pill entirely
    return null;
  }

  const colorClass = (() => {
    switch (status.state) {
      case 'connected':
        return 'gspro-pill--green';
      case 'reconnecting':
      case 'connecting':
        return 'gspro-pill--amber';
      default:
        return 'gspro-pill--gray';
    }
  })();

  const label =
    status.state === 'connected'
      ? 'GSPro: Connected'
      : status.state === 'reconnecting'
        ? `GSPro: Reconnecting (${status.next_retry_in_s.toFixed(0)}s)`
        : `GSPro: ${status.state.charAt(0).toUpperCase()}${status.state.slice(1)}`;

  const tooltip = `${status.host}:${status.port}${status.message ? ' — ' + status.message : ''}`;

  return (
    <div className={`gspro-pill ${colorClass}`} title={tooltip}>
      <span className="gspro-pill__dot" />
      <span className="gspro-pill__text">{label}</span>
    </div>
  );
}
