//+------------------------------------------------------------------+
//|                                                SlaveObserver.mq5 |
//|                        Observer EA for positions and orders       |
//+------------------------------------------------------------------+
#property strict

input int UpdateSeconds = 1;
input bool UseCommonFolder = true;
input ushort CsvSeparator = 9;
input string TerminalId = "master_1";
input string FilePrefix = "slave";

string positions_file;
string orders_file;
string heartbeat_file;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   positions_file = FilePrefix + "_positions.csv";
   orders_file = FilePrefix + "_orders.csv";
   heartbeat_file = FilePrefix + "_heartbeat.csv";

   EventSetTimer(UpdateSeconds);
   Print("SlaveObserver iniciado. TerminalId=", TerminalId, ". Files: ", positions_file, ", ", orders_file, ", ", heartbeat_file);
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("SlaveObserver detenido.");
}

//+------------------------------------------------------------------+
//| Timer function                                                   |
//+------------------------------------------------------------------+
void OnTimer()
{
   ExportPositions();
   ExportOrders();
   ExportHeartbeat();
}

//+------------------------------------------------------------------+
//| Get file flags                                                   |
//+------------------------------------------------------------------+
int GetFileFlags()
{
   int flags = FILE_WRITE | FILE_CSV | FILE_ANSI;

   if(UseCommonFolder)
      flags = flags | FILE_COMMON;

   return flags;
}

//+------------------------------------------------------------------+
//| Open CSV file with configured separator                          |
//+------------------------------------------------------------------+
int OpenCsvFile(string file_name)
{
   return FileOpen(file_name, GetFileFlags(), CsvSeparator);
}

//+------------------------------------------------------------------+
//| Convert position type to text                                    |
//+------------------------------------------------------------------+
string PositionTypeToString(long type)
{
   if(type == POSITION_TYPE_BUY)
      return "BUY";

   if(type == POSITION_TYPE_SELL)
      return "SELL";

   return "UNKNOWN";
}

//+------------------------------------------------------------------+
//| Convert order type to text                                       |
//+------------------------------------------------------------------+
string OrderTypeToString(ENUM_ORDER_TYPE type)
{
   switch(type)
   {
      case ORDER_TYPE_BUY:
         return "BUY";

      case ORDER_TYPE_SELL:
         return "SELL";

      case ORDER_TYPE_BUY_LIMIT:
         return "BUY_LIMIT";

      case ORDER_TYPE_SELL_LIMIT:
         return "SELL_LIMIT";

      case ORDER_TYPE_BUY_STOP:
         return "BUY_STOP";

      case ORDER_TYPE_SELL_STOP:
         return "SELL_STOP";

      case ORDER_TYPE_BUY_STOP_LIMIT:
         return "BUY_STOP_LIMIT";

      case ORDER_TYPE_SELL_STOP_LIMIT:
         return "SELL_STOP_LIMIT";

      default:
         return "UNKNOWN";
   }
}

//+------------------------------------------------------------------+
//| Export open positions                                            |
//+------------------------------------------------------------------+
void ExportPositions()
{
   int handle = OpenCsvFile(positions_file);

   if(handle == INVALID_HANDLE)
   {
      Print("Error abriendo archivo de posiciones: ", GetLastError());
      return;
   }

   FileWrite(
      handle,
      "ticket",
      "symbol",
      "type",
      "volume",
      "price_open",
      "sl",
      "tp",
      "profit",
      "swap",
      "commission",
      "magic",
      "comment",
      "time_open",
      "time_update"
   );

   int total = PositionsTotal();

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);

      if(ticket == 0)
         continue;

      if(!PositionSelectByTicket(ticket))
         continue;

      string symbol       = PositionGetString(POSITION_SYMBOL);
      long type           = PositionGetInteger(POSITION_TYPE);
      double volume       = PositionGetDouble(POSITION_VOLUME);
      double price_open   = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl           = PositionGetDouble(POSITION_SL);
      double tp           = PositionGetDouble(POSITION_TP);
      double profit       = PositionGetDouble(POSITION_PROFIT);
      double swap         = PositionGetDouble(POSITION_SWAP);
      double commission   = PositionGetDouble(POSITION_COMMISSION);
      long magic          = PositionGetInteger(POSITION_MAGIC);
      string comment      = PositionGetString(POSITION_COMMENT);
      datetime time_open  = (datetime)PositionGetInteger(POSITION_TIME);

      FileWrite(
         handle,
         (string)ticket,
         symbol,
         PositionTypeToString(type),
         DoubleToString(volume, 2),
         DoubleToString(price_open, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
         DoubleToString(sl, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
         DoubleToString(tp, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
         DoubleToString(profit, 2),
         DoubleToString(swap, 2),
         DoubleToString(commission, 2),
         (string)magic,
         comment,
         TimeToString(time_open, TIME_DATE | TIME_SECONDS),
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS)
      );
   }

   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Export pending orders                                            |
//+------------------------------------------------------------------+
void ExportOrders()
{
   int handle = OpenCsvFile(orders_file);

   if(handle == INVALID_HANDLE)
   {
      Print("Error abriendo archivo de órdenes pendientes: ", GetLastError());
      return;
   }

   FileWrite(
      handle,
      "ticket",
      "symbol",
      "type",
      "volume_initial",
      "volume_current",
      "price_open",
      "sl",
      "tp",
      "magic",
      "comment",
      "time_setup",
      "time_expiration",
      "time_update"
   );

   int total = OrdersTotal();

   for(int i = 0; i < total; i++)
   {
      ulong ticket = OrderGetTicket(i);

      if(ticket == 0)
         continue;

      if(!OrderSelect(ticket))
         continue;

      string symbol              = OrderGetString(ORDER_SYMBOL);
      ENUM_ORDER_TYPE type       = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      double volume_initial      = OrderGetDouble(ORDER_VOLUME_INITIAL);
      double volume_current      = OrderGetDouble(ORDER_VOLUME_CURRENT);
      double price_open          = OrderGetDouble(ORDER_PRICE_OPEN);
      double sl                  = OrderGetDouble(ORDER_SL);
      double tp                  = OrderGetDouble(ORDER_TP);
      long magic                 = OrderGetInteger(ORDER_MAGIC);
      string comment             = OrderGetString(ORDER_COMMENT);
      datetime time_setup        = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
      datetime time_expiration   = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);

      FileWrite(
         handle,
         (string)ticket,
         symbol,
         OrderTypeToString(type),
         DoubleToString(volume_initial, 2),
         DoubleToString(volume_current, 2),
         DoubleToString(price_open, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
         DoubleToString(sl, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
         DoubleToString(tp, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)),
         (string)magic,
         comment,
         TimeToString(time_setup, TIME_DATE | TIME_SECONDS),
         TimeToString(time_expiration, TIME_DATE | TIME_SECONDS),
         TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS)
      );
   }

   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Export heartbeat                                                 |
//+------------------------------------------------------------------+
void ExportHeartbeat()
{
   int handle = OpenCsvFile(heartbeat_file);

   if(handle == INVALID_HANDLE)
   {
      Print("Error abriendo heartbeat: ", GetLastError());
      return;
   }

   FileWrite(handle, "terminal_id", "role", "status", "server_time", "account_login", "account_server", "positions_total", "orders_total");
   FileWrite(
      handle,
      TerminalId,
      "source",
      "RUNNING",
      TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
      (string)AccountInfoInteger(ACCOUNT_LOGIN),
      AccountInfoString(ACCOUNT_SERVER),
      PositionsTotal(),
      OrdersTotal()
   );

   FileClose(handle);
}
