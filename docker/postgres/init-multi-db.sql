-- Create additional databases needed by the unified appliance baseline.
-- NOTE: Init scripts run only on first boot of a fresh volume.
-- `CREATE DATABASE` cannot run inside a DO/function, so keep this as plain SQL.

CREATE DATABASE dify;
CREATE DATABASE dify_plugin;

GRANT ALL PRIVILEGES ON DATABASE dify TO atlas;
GRANT ALL PRIVILEGES ON DATABASE dify_plugin TO atlas;
