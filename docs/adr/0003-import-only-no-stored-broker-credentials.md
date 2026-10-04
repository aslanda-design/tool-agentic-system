# Launch with Import only; store no broker credentials

At launch, broker data arrives only by Users uploading statements. The live IBKR adapter needs a local TWS socket and cannot exist in a hosted service, and a stored Flex token grants read access to a User's full account statements. Not holding broker credentials removes the most damaging thing we could leak. Flex-token Sync may come later behind envelope encryption with a KMS-held key.

Considered: a broker aggregator (SnapTrade, Tink) — wider coverage but an extra processor, cost, and weak coverage of MyInvestor.
