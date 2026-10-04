# Investment Tracker

A personal-investment tracking product: users bring their positions and transaction history from multiple brokers into one place and see portfolio value against amount invested over time. Moving from a single-user local app to a multi-user EU-hosted SaaS.

## Language

### People and ownership

**User**:
A person with a login identity who owns a private set of portfolio data. The unit of tenancy.
_Avoid_: Account (reserved for Brokerage Account), customer, member

**Brokerage Account**:
One account a User holds at a broker (e.g. an Interactive Brokers account, a MyInvestor account). Always belongs to exactly one User.
_Avoid_: Account (ambiguous with User login), portfolio

**Tenant**:
Synonym for a User's isolated data scope. Used only when discussing isolation guarantees.
_Avoid_: Organisation, workspace (there is no multi-user sharing)

### Data ownership

**Private data**:
Anything derived from a User's own holdings or activity: Brokerage Accounts, holdings, transactions, cash balances, snapshots, notes, chat, quant runs, agent runs. Carries a User owner and is never visible to another User.
_Avoid_: User data (too vague), tenant data

**Reference data**:
Public market facts stored once and shared by every User: assets and their identifiers, prices, quotes, FX rates, the market-intel cache, and security resolutions. Contains nothing about any User.
_Avoid_: Global data, shared data

**Security resolution**:
The decision of which market-data listing prices a given security. A fact about the security, not about a User, so it is Reference data and is reviewed by the operator, not by Users.
_Avoid_: Asset mapping (legacy name)

### Ingestion

**Import**:
A User uploading a broker statement (CSV or XLSX) that becomes transactions. The only way broker data enters the product at launch.
_Avoid_: Sync (reserved for live broker API connections, not offered at launch)

**Sync**:
A live pull from a broker API using a stored credential. Deferred past launch because it requires storing broker credentials.

### Posture

**Informational use**:
The product's legal posture: it reports and analyses what a User already holds and never recommends buying, selling or holding. Analytics, Quant Lab and the assistant are all framed this way.
_Avoid_: Advice, recommendation, signal

**Operator**:
The person running the service (currently the founder). Reviews Security resolutions and administers the platform; has no routine access to Private data.
_Avoid_: Admin (ambiguous with User roles)
