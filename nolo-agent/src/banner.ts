const RESET = '\x1b[0m';
const BOLD = '\x1b[1m';
const DIM = '\x1b[2m';
const CYAN = '\x1b[36m';
const MAGENTA = '\x1b[35m';

const LOGO = `
  ███╗   ██╗ ██████╗ ██╗      ██████╗
  ████╗  ██║██╔═══██╗██║     ██╔═══██╗
  ██╔██╗ ██║██║   ██║██║     ██║   ██║
  ██║╚██╗██║██║   ██║██║     ██║   ██║
  ██║ ╚████║╚██████╔╝███████╗╚██████╔╝
  ╚═╝  ╚═══╝ ╚═════╝ ╚══════╝ ╚═════╝`;

export interface BannerInfo {
  model: string;
  cwd: string;
  approvalPolicy: string;
  slashCommands: boolean;
  version: string;
}

export function printBanner(info: BannerInfo): void {
  console.log(MAGENTA + BOLD + LOGO + RESET);
  console.log(`  ${DIM}BlaqVibes coding agent${RESET}  ${DIM}v${info.version}${RESET}  ${DIM}·  powered by OpenRouter${RESET}\n`);
  console.log(`  ${DIM}model   ${RESET}${CYAN}${info.model}${RESET}`);
  console.log(`  ${DIM}cwd     ${RESET}${info.cwd}`);
  console.log(`  ${DIM}approve ${RESET}${info.approvalPolicy}`);
  console.log(`  ${DIM}guard   ${RESET}${DIM}workspace only · .env/keys/db/media/.git protected · shell env scrubbed · secrets redacted${RESET}`);
  if (info.slashCommands) console.log(`  ${DIM}type /help for commands, /workspace for the boundary, "exit" to quit${RESET}`);
  console.log();
}
