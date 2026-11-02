--!strict
-- Keeps the client listener alive so [CLIENTBRIDGE:1] lines always reach the log.
-- Your UI can call ClientBridge.onEvent / optIn / optOut / getSession.

local ClientBridge = require(game.ReplicatedStorage.ClientBridge)
