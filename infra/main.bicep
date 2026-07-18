targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment; used to derive a short unique resource token.')
param environmentName string

@minLength(1)
@description('Primary Azure region for all resources (must support Flex Consumption).')
param location string

@description('Polygon.io API key (covers stocks + crypto).')
@secure()
param polygonApiKey string

@description('Alpaca API key ID (free real-time stocks + crypto).')
param alpacaKeyId string = ''

@description('Alpaca API secret key.')
@secure()
param alpacaSecret string = ''

@description('Market data provider: Alpaca | Polygon | Mock.')
param marketProvider string = 'Alpaca'

@description('Options data provider: Tradier | Mock | LicensedHttp.')
param optionsProvider string = 'Tradier'

@description('Tradier API token for real options chains.')
@secure()
param tradierApiKey string = ''

@description('Tradier base URL: https://api.tradier.com for live or https://sandbox.tradier.com for sandbox.')
param tradierBaseUrl string = 'https://api.tradier.com'

@description('Gmail SMTP username / sender address (used for both From and auth).')
param emailUsername string

@description('Gmail SMTP app password (16-char app password, NOT the account password).')
@secure()
param emailPassword string

@description('Report recipient email address.')
param emailTo string = 'bkotar1@live.com'

@description('Discord webhook URL for intraday alerts (optional).')
@secure()
param discordWebhookUrl string = ''

@description('When true, the bot logs reports but does not actually send email.')
param dryRun string = 'true'

var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = { 'azd-env-name': environmentName }

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources'
  scope: rg
  params: {
    location: location
    resourceToken: resourceToken
    tags: tags
    polygonApiKey: polygonApiKey
    alpacaKeyId: alpacaKeyId
    alpacaSecret: alpacaSecret
    marketProvider: marketProvider
    optionsProvider: optionsProvider
    tradierApiKey: tradierApiKey
    tradierBaseUrl: tradierBaseUrl
    emailUsername: emailUsername
    emailPassword: emailPassword
    emailTo: emailTo
    discordWebhookUrl: discordWebhookUrl
    dryRun: dryRun
  }
}

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output SERVICE_BOT_NAME string = resources.outputs.functionAppName
output SERVICE_BOT_URI string = resources.outputs.functionAppUri
