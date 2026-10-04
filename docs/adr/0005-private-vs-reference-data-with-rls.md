# Split Private data from Reference data; enforce isolation with RLS

Private data tables get a user owner column and Postgres Row-Level Security, plus a user-scoped repository layer in the application; Reference data (assets, prices, quotes, FX, market-intel cache, security resolutions) is stored once and shared. A cross-tenant leak test runs in CI. Schema-per-user was rejected: it multiplies migrations and operations for a product with small per-user data.

Consequence: today `assets.symbol` is globally unique and manual entry can create arbitrary assets. In a shared table, one User's free-text asset would pollute everyone's; user-created assets need a separate path or a per-user alias layer.
