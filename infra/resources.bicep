@description('Azure region for all resources.')
param location string

@description('Short unique token used to name globally-unique resources.')
param resourceToken string

@description('Tags applied to all resources.')
param tags object

@secure()
param polygonApiKey string

param alpacaKeyId string = ''

@secure()
param alpacaSecret string = ''

param marketProvider string = 'Alpaca'

param emailUsername string

@secure()
param emailPassword string

param emailTo string

@secure()
param discordWebhookUrl string = ''

param dryRun string

var functionAppName = 'func-${resourceToken}'
var planName = 'plan-${resourceToken}'
var storageName = 'st${resourceToken}'
var deploymentContainerName = 'deploymentpackage'

// ---------------------------------------------------------------------------
// Storage (required by Functions; also hosts the Flex Consumption deploy package)
// ---------------------------------------------------------------------------
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: deploymentContainerName
  properties: { publicAccess: 'None' }
}

// ---------------------------------------------------------------------------
// Observability
// ---------------------------------------------------------------------------
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${resourceToken}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${resourceToken}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ---------------------------------------------------------------------------
// Flex Consumption plan + Function App (.NET 8 isolated)
// ---------------------------------------------------------------------------
resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  tags: tags
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  tags: union(tags, { 'azd-service-name': 'bot' })
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: {
        name: 'dotnet-isolated'
        version: '8.0'
      }
    }
    siteConfig: {
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }

        // --- Bot configuration ---
        { name: 'Bot__MarketProvider', value: marketProvider }
        { name: 'Bot__Providers__Polygon__ApiKey', value: polygonApiKey }
        { name: 'Bot__Providers__Polygon__MinRequestIntervalMs', value: '13000' }
        { name: 'Bot__Providers__Alpaca__ApiKeyId', value: alpacaKeyId }
        { name: 'Bot__Providers__Alpaca__ApiSecret', value: alpacaSecret }
        { name: 'Bot__Providers__Alpaca__Feed', value: 'iex' }
        { name: 'Bot__Providers__Alpaca__PaperTradingBaseUrl', value: 'https://paper-api.alpaca.markets' }
        { name: 'Bot__TimeZone', value: 'Eastern Standard Time' }
        { name: 'Bot__MarketOpenLocal', value: '08:30' }
        { name: 'Bot__MarketCloseLocal', value: '15:00' }
        { name: 'Bot__CryptoCloseLocal', value: '22:00' }
        { name: 'Bot__MaxCandidates', value: '10' }
        { name: 'Bot__DryRun', value: dryRun }

        { name: 'Bot__StockUniverse', value: 'NVDA,AMD,AAPL,MSFT,META,AMZN,GOOGL,TSLA,AVGO,NFLX,CRM,PLTR,COIN,SMCI,MU' }
        { name: 'Bot__CryptoUniverse', value: 'BTC-USD,ETH-USD,SOL-USD,XRP-USD,DOGE-USD,ADA-USD' }

        { name: 'Bot__Universe__Mode', value: 'Dynamic' }
        { name: 'Bot__Universe__TopN', value: '40' }
        { name: 'Bot__Universe__MinPrice', value: '5' }
        { name: 'Bot__Universe__MinVolume', value: '1000000' }
        { name: 'Bot__Universe__IncludeMostActives', value: 'true' }
        { name: 'Bot__Universe__IncludeGainers', value: 'true' }
        { name: 'Bot__Universe__IncludeLosers', value: 'false' }
        { name: 'Bot__Universe__AlwaysInclude', value: 'NVDA,AMD,AVGO,MSFT,META,AMZN,GOOGL,AAPL' }

        { name: 'Bot__Strategy__Mode', value: 'BreakoutVolume' }
        { name: 'Bot__Strategy__Stock__MinVolumeRatio', value: '1.5' }
        { name: 'Bot__Strategy__Stock__MinAdx', value: '20' }
        { name: 'Bot__Strategy__Stock__RequireAbove200Sma', value: 'true' }
        { name: 'Bot__Strategy__Crypto__MinVolumeRatio', value: '2.0' }
        { name: 'Bot__Strategy__Crypto__MinAdx', value: '20' }
        { name: 'Bot__Strategy__Crypto__RequireAbove200Sma', value: 'true' }

        // --- Institutional stock overlays (observation-only during shadow phase) ---
        { name: 'Bot__Institutional__Enabled', value: 'true' }
        { name: 'Bot__Institutional__ShadowOnly', value: 'true' }
        { name: 'Bot__Institutional__RelativeStrengthLookbackDays', value: '20' }
        { name: 'Bot__Institutional__MinSectorExcessReturnPct', value: '1.5' }
        { name: 'Bot__Institutional__PairLookbackDays', value: '60' }
        { name: 'Bot__Institutional__MinPairCorrelation', value: '0.70' }

        // --- Alpaca stock paper orders; explicitly disarmed until paper keys are verified ---
        { name: 'Bot__PaperTrading__Enabled', value: 'true' }
        { name: 'Bot__PaperTrading__SubmitToAlpaca', value: 'false' }
        { name: 'Bot__PaperTrading__CapitalPerTradeUsd', value: '1000' }
        { name: 'Bot__PaperTrading__MaxOpenPositions', value: '10' }
        { name: 'Bot__PaperTrading__MaxNewPositionsPerDay', value: '2' }
        { name: 'Bot__PaperTrading__MinCandidateScore', value: '70' }
        { name: 'Bot__PaperTrading__MaxAccountExposurePct', value: '25' }
        { name: 'Bot__PaperTrading__RiskPerTradePct', value: '0.5' }

        // --- Crypto scalp strategy (15-minute + 4-hour, VWAP/MACD, 10-20% targets) ---
        { name: 'Bot__Scalp__Enabled', value: 'true' }
        { name: 'Bot__Scalp__Mode', value: 'Dynamic' }
        { name: 'Bot__Scalp__TopN', value: '15' }
        { name: 'Bot__Scalp__ScreenerTimeframe', value: '4Hour' }
        { name: 'Bot__Scalp__MinChangePct', value: '2.0' }
        { name: 'Bot__Scalp__SortBy', value: 'movement' }
        { name: 'Bot__Scalp__AlwaysInclude', value: 'BTC-USD,ETH-USD' }
        { name: 'Bot__Scalp__HigherTimeframe', value: '4Hour' }
        { name: 'Bot__Scalp__EntryTimeframe', value: '15Min' }
        { name: 'Bot__Scalp__Target1Pct', value: '10' }
        { name: 'Bot__Scalp__Target2Pct', value: '20' }
        { name: 'Bot__Scalp__MaxStopPct', value: '4' }
        { name: 'Bot__Scalp__StopAtrMult', value: '1.0' }
        { name: 'Bot__Scalp__MinRewardRisk', value: '2.0' }
        { name: 'Bot__Scalp__MinEntryVolumeRatio', value: '1.5' }
        { name: 'Bot__Scalp__MinEntryRsi', value: '50' }
        { name: 'Bot__Scalp__MaxEntryRsi', value: '72' }
        { name: 'Bot__Scalp__MinHigherAdx', value: '20' }

        { name: 'Bot__Options__Enabled', value: 'true' }
        { name: 'Bot__Options__Provider', value: 'Mock' }

        // --- Notifications: Discord intraday alerts + daily email digest ---
        { name: 'Bot__Notifications__Provider', value: 'Email' }
        { name: 'Bot__Notifications__IntradayChannel', value: 'Discord' }
        { name: 'Bot__Notifications__DailyChannel', value: 'Email' }
        { name: 'Bot__Notifications__SuppressEmpty', value: 'true' }
        { name: 'Bot__Notifications__Discord__WebhookUrl', value: discordWebhookUrl }
        { name: 'Bot__Notifications__Email__SmtpHost', value: 'smtp.gmail.com' }
        { name: 'Bot__Notifications__Email__SmtpPort', value: '587' }
        { name: 'Bot__Notifications__Email__UseSsl', value: 'true' }
        { name: 'Bot__Notifications__Email__Username', value: emailUsername }
        { name: 'Bot__Notifications__Email__Password', value: emailPassword }
        { name: 'Bot__Notifications__Email__From', value: emailUsername }
        { name: 'Bot__Notifications__Email__To', value: emailTo }
      ]
    }
  }
}

// ---------------------------------------------------------------------------
// RBAC: grant the Function App's managed identity data access to its storage
// (Storage Blob Data Owner — required for Flex Consumption deploy + WebJobs host)
// ---------------------------------------------------------------------------
resource storageBlobDataOwner 'Microsoft.Authorization/roleDefinitions@2022-04-01' existing = {
  scope: subscription()
  name: 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
}

resource blobRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, functionApp.id, storageBlobDataOwner.id)
  scope: storage
  properties: {
    roleDefinitionId: storageBlobDataOwner.id
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output functionAppName string = functionApp.name
output functionAppUri string = 'https://${functionApp.properties.defaultHostName}'
