--!strict
-- Defaults. Everything can be overridden in ClientBridge.start{...}.

return {
	transports = { console = true, http = false },
	endpoint = nil,
	secretName = nil,
	signingKey = nil,
	events = nil,
	allowClientOrigin = true,
	consoleRequiresOptIn = false,
	maxEventBytes = 4096,
	http = {
		batchSize = 100,
		flushSec = 5,
		budgetPerMinute = 400,
		maxRetries = 4,
	},
	session = {
		ttlSec = 86400,
	},
}
