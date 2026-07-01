from datetime import datetime
import time
import pandas as pd
import numpy as np
from statsmodels.tsa.arima.model import ARIMA
from prophet import Prophet
from xgboost import XGBRegressor
from sklearn.preprocessing import MinMaxScaler
from keras.models import Sequential
from keras.layers import Dense, LSTM
from sklearn.metrics import mean_squared_error
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from src.agent_orch.utils.DBConnect import DBConnect
from sklearn.linear_model import LinearRegression, ElasticNet
from sklearn.ensemble import RandomForestRegressor
from sklearn.neighbors import KNeighborsRegressor
from sklearn.svm import SVR
from src.agent_orch.utils import DBConnect
#db= DBConnect()
#table="vm_master"
#rows=db.select(table=table,columns=["vm_id","name"])

#if rows:
#    data1 = pd.DataFrame(rows)
#    server_data=data1

# Function to prepare data for LSTM
def prepare_lstm_data(series, n_lags):
    X, y = [], []
    for i in range(n_lags, len(series)):
        X.append(series[i-n_lags:i])
        y.append(series[i])
    return np.array(X), np.array(y)
 
# Function to prepare data for XGBoost and other regressors
def create_lag_features(df, lag=1):
    df_lagged = df.copy()
    for i in range(1, lag+1):
        df_lagged[f'lag_{i}'] = df['avg_cpu_usage'].shift(i)
    return df_lagged.dropna()

def create_lag_features1(df, lag=1):
    df_lagged = df.copy()
    for i in range(1, lag + 1):
        df_lagged[f'lag_{i}'] = df['avg_cpu_usage'].shift(i)  # Create lagged features
    df_lagged = df_lagged.dropna()  # Drop rows with NaN values caused by shifting
 
    # Separate features and target
    X_train = df_lagged[['date'] + [f'lag_{i}' for i in range(1, lag + 1)]]
    y_train = df_lagged['avg_cpu_usage']
 
    return X_train, y_train



def insert_db(in_data, run_id):
    in_data['yhat'] = in_data['yhat'].round(2)
    inserted_by = "ForecastingAgent"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for _, row in in_data.iterrows():
        record = {
            "run_id": run_id,
            "dop": row["ds"].strftime("%Y-%m-%d"),  # forecast date as string
            "alg": row["model"],
            "p_avg_usage": float(row["yhat"]),
            "inserted_by": inserted_by,
            "inserted_date": now,
            "updated_by": inserted_by,
            "updated_date": now
        }
        db= DBConnect.DBConnect()
        db.insert("forecast_results", record)
        db.close()


def ARIMA_model(train,test,days_predicit,forecasts,errors,server_id,r_type,table):
    # ARIMA model
    arima_series = train['avg_cpu_usage']
    arima_model = ARIMA(arima_series, order=(5,1,0))
    arima_model_fit = arima_model.fit()
    arima_forecast = arima_model_fit.forecast(steps=days_predicit)
    arima_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=test['date'].min(), periods=days_predicit, freq='D'),
        'yhat': arima_forecast,
        'model': 'ARIMA',
        'server_id': server_id
    })
    if not test.empty:
        arima_mse = mean_squared_error(test['avg_cpu_usage'], arima_forecast)
        va=np.var(test['avg_cpu_usage'])
        ep=(arima_mse/va)*100
        errors['ARIMA'].append(ep)
    forecasts['ARIMA'].append(arima_forecast_df)
    # insert_db(arima_forecast_df,r_type,table)
    return arima_model_fit

def SARIMA_model(train,test,days_predicit,forecasts,errors,server_id,r_type,table):
    arima_series = train['avg_cpu_usage']
    sarima_model = SARIMAX(arima_series, order=(1, 1, 1), seasonal_order=(1, 1, 1, 12))
    sarima_model_fit = sarima_model.fit(disp=False)
    sarima_forecast = sarima_model_fit.forecast(steps=days_predicit)
    sarima_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=test['date'].min(), periods=days_predicit, freq='D'),
        'yhat': sarima_forecast,
        'model': 'SARIMA',
        'server_id': server_id
    })
    if not test.empty:
        sarima_mse = mean_squared_error(test['avg_cpu_usage'], sarima_forecast)
        va=np.var(test['avg_cpu_usage'])
        ep=(sarima_mse/va)*100
        errors['SARIMA'].append(ep)
        #errors['SARIMA'].append(sarima_mse)
    forecasts['SARIMA'].append(sarima_forecast_df)
    return sarima_model_fit

def Holt_Winters_model(train,test,days_predicit,forecasts,errors,server_id,r_type,table):   
    arima_series = train['avg_cpu_usage']
    holt_model = ExponentialSmoothing(arima_series, seasonal='add', seasonal_periods=12)
    holt_model_fit = holt_model.fit()
    holt_forecast = holt_model_fit.forecast(steps=days_predicit)
    holt_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=test['date'].min(), periods=days_predicit, freq='D'),
        'yhat': holt_forecast,
        'model': 'Holt-Winters',
        'server_id': server_id
    })
    if not test.empty:
        holt_mse = mean_squared_error(test['avg_cpu_usage'], holt_forecast)
        va=np.var(test['avg_cpu_usage'])
        ep=(holt_mse/va)*100
        errors['Holt-Winters'].append(ep)
        #errors['Holt-Winters'].append(holt_mse)
    forecasts['Holt-Winters'].append(holt_forecast_df)
    return holt_model

def RFR_model(train,test,days_predicit,forecasts,errors,server_id,r_type,table):
    xgb_train = create_lag_features(train, lag=5)
    X_train = xgb_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = xgb_train['avg_cpu_usage']
    xgb_test = create_lag_features(test, lag=5)
    X_test = xgb_test.drop(columns=['date', 'avg_cpu_usage'])
    rf_model = RandomForestRegressor(n_estimators=100)
    rf_model.fit(X_train, y_train)
    rf_forecast = rf_model.predict(X_test)
    rf_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=test['date'].min(), periods=days_predicit, freq='D'),
        'yhat': rf_forecast,
        'model': 'Random Forest',
        'server_id': server_id
    })
    if not test.empty:
        rf_mse = mean_squared_error(test['avg_cpu_usage'], rf_forecast)
        va=np.var(test['avg_cpu_usage'])
        ep=(rf_mse/va)*100
        errors['Random Forest'].append(ep)
    forecasts['Random Forest'].append(rf_forecast_df)
    return rf_model

def RFR_model1(train,test,days_predicit,forecasts,errors,server_id,r_type,table):
    #xgb_train = create_lag_features(train, lag=5)
    X_train = train[['date']]
    #X_train,y_train= create_lag_features1(train, lag=5)
    #X_train = X_train.drop(columns=['date'])
    y_train = train['avg_cpu_usage']
    X_test=test[['date']]
    y_test=test['avg_cpu_usage']
    rf_model = RandomForestRegressor(n_estimators=100,random_state=42)
    rf_model.fit(X_train, y_train)
    rf_forecast = rf_model.predict(X_test)
    rf_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=test['date'].min(), periods=days_predicit, freq='D'),
        'yhat': rf_forecast,
        'model': 'Random Forest',
        'server_id': server_id
    })
    if not test.empty:
        rf_mse = mean_squared_error(y_test, rf_forecast)
        errors['Random Forest'].append(rf_mse)
    forecasts['Random Forest'].append(rf_forecast_df)
    return rf_model

def Prophet_Model(train,test,days_predicit,forecasts,errors,server_id,r_type,table):
    prophet_df = train[['date', 'avg_cpu_usage']].rename(columns={'date': 'ds', 'avg_cpu_usage': 'y'})
    prophet_model = Prophet()
    prophet_model.fit(prophet_df)
    prophet_future = prophet_model.make_future_dataframe(periods=days_predicit)
    prophet_forecast = prophet_model.predict(prophet_future).tail(days_predicit)
    prophet_forecast_df = prophet_forecast[['ds', 'yhat']]
    prophet_forecast_df['model'] = 'Prophet'
    prophet_forecast_df['server_id'] = server_id
    prophet_mse = mean_squared_error(test['avg_cpu_usage'], prophet_forecast['yhat'])
    forecasts['Prophet'].append(prophet_forecast_df)
    va=np.var(test['avg_cpu_usage'])
    ep=(prophet_mse/va)*100
    errors['Prophet'].append(ep)
    return prophet_model


def XGBoost_Model(train,test,days_predicit,forecasts,errors,server_id,r_type,table):
    xgb_train = create_lag_features(train, lag=5)
    X_train = xgb_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = xgb_train['avg_cpu_usage']
    xgb_model = XGBRegressor()
    xgb_model.fit(X_train, y_train) 
    xgb_test = create_lag_features(test, lag=5).iloc[-days_predicit:]
    X_test = xgb_test.drop(columns=['date', 'avg_cpu_usage'])
    xgb_forecast = xgb_model.predict(X_test)
    n = min(len(xgb_forecast), days_predicit)
    xgb_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=test['date'].min(), periods=n, freq='D'),
        'yhat': xgb_forecast[:n],
        'model': 'XGBoost',
        'server_id': server_id
    })
    if not test.empty:
        actuals = test['avg_cpu_usage'].values[-n:]
        xgb_mse = mean_squared_error(actuals, xgb_forecast[:n])
        va = np.var(actuals)
        ep = (xgb_mse / va) * 100 if va != 0 else 0
        errors['XGBoost'].append(ep)
    #xgb_mse = mean_squared_error(test['avg_cpu_usage'], xgb_forecast)
    forecasts['XGBoost'].append(xgb_forecast_df)
    #va=np.var(test['avg_cpu_usage'])
    #ep=(xgb_mse/va)*100
    #errors['XGBoost'].append(ep)
    return xgb_model



def LSTM_Model(train,test,days_predicit,forecasts,errors,server_id,r_type,table):
    lstm_series = train['avg_cpu_usage'].values
    n_lags = 10
    X, y = prepare_lstm_data(lstm_series, n_lags)
    lstm_model = Sequential([
        LSTM(50, activation='relu', input_shape=(n_lags, 1)),
        Dense(1)
    ])
    lstm_model.compile(optimizer='adam', loss='mse')
    lstm_model.fit(X, y, epochs=50, verbose=0)
    
    lstm_input = lstm_series[-n_lags:]
    lstm_predictions = []
    for _ in range(days_predicit):
        lstm_input_reshaped = lstm_input.reshape((1, n_lags, 1))
        prediction = lstm_model.predict(lstm_input_reshaped, verbose=0)
        lstm_predictions.append(prediction[0][0])
        lstm_input = np.append(lstm_input[1:], prediction)
    
    lstm_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=test['date'].min(), periods=days_predicit, freq='D'),
        'yhat': lstm_predictions,
        'model': 'LSTM',
        'server_id': server_id
    })
    lstm_mse = mean_squared_error(test['avg_cpu_usage'], lstm_predictions)
    forecasts['LSTM'].append(lstm_forecast_df)
    va=np.var(test['avg_cpu_usage'])
    ep=(lstm_mse/va)*100
    errors['LSTM'].append(ep)
    return lstm_model

def LR_Model(train,test,days_predicit,forecasts,errors,server_id,r_type,table):
    lr_train = create_lag_features(train, lag=5)
    X_train = lr_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = lr_train['avg_cpu_usage']
    lr_model = LinearRegression()
    lr_model.fit(X_train, y_train)
    lr_test = create_lag_features(test, lag=5)
    X_test = lr_test.drop(columns=['date', 'avg_cpu_usage'])
    lr_forecast = lr_model.predict(X_test)
    lr_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=test['date'].min(), periods=days_predicit, freq='D'),
        'yhat': lr_forecast,
        'model': 'Linear Regression',
        'server_id': server_id
    })
    lr_mse = mean_squared_error(test['avg_cpu_usage'], lr_forecast)
    forecasts['Linear Regression'].append(lr_forecast_df)
    va=np.var(test['avg_cpu_usage'])
    ep=(lr_mse/va)*100
    errors['Linear Regression'].append(ep)
    return lr_model

def KNN_Model(train,test,days_predicit,forecasts,errors,server_id,r_type,table):
    xgb_train = create_lag_features(train, lag=5)
    X_train = xgb_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = xgb_train['avg_cpu_usage']
    xgb_test = create_lag_features(test, lag=5)
    X_test = xgb_test.drop(columns=['date', 'avg_cpu_usage'])
    knn_model = KNeighborsRegressor(n_neighbors=5)
    knn_model.fit(X_train, y_train)
    knn_forecast = knn_model.predict(X_test)
    knn_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=test['date'].min(), periods=days_predicit, freq='D'),
        'yhat': knn_forecast,
        'model': 'KNN',
        'server_id': server_id
    })
    knn_mse = mean_squared_error(test['avg_cpu_usage'], knn_forecast)
    forecasts['KNN'].append(knn_forecast_df)
    va=np.var(test['avg_cpu_usage'])
    ep=(knn_mse/va)*100
    errors['KNN'].append(ep)
    return knn_model

def EN_Model(train,test,days_predicit,forecasts,errors,server_id,r_type,table):
    xgb_train = create_lag_features(train, lag=5)
    X_train = xgb_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = xgb_train['avg_cpu_usage']
    xgb_test = create_lag_features(test, lag=5)
    X_test = xgb_test.drop(columns=['date', 'avg_cpu_usage'])
    en_model = ElasticNet()
    en_model.fit(X_train, y_train)
    en_forecast = en_model.predict(X_test)
    en_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=test['date'].min(), periods=days_predicit, freq='D'),
        'yhat': en_forecast,
        'model': 'Elastic Net',
        'server_id': server_id
    })
    en_mse = mean_squared_error(test['avg_cpu_usage'], en_forecast)
    forecasts['Elastic Net'].append(en_forecast_df)
    va=np.var(test['avg_cpu_usage'])
    ep=(en_mse/va)*100
    errors['Elastic Net'].append(ep)

def SVR_Model(train,test,days_predicit,forecasts,errors,server_id,r_type,table):
    xgb_train = create_lag_features(train, lag=5)
    X_train = xgb_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = xgb_train['avg_cpu_usage']
    xgb_test = create_lag_features(test, lag=5)
    X_test = xgb_test.drop(columns=['date', 'avg_cpu_usage'])
    svr_model = SVR()
    svr_model.fit(X_train, y_train)
    svr_forecast = svr_model.predict(X_test)
    svr_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=test['date'].min(), periods=days_predicit, freq='D'),
        'yhat': svr_forecast,
        'model': 'SVR',
        'server_id': server_id
    })
    svr_mse = mean_squared_error(test['avg_cpu_usage'], svr_forecast)
    forecasts['SVR'].append(svr_forecast_df)
    va=np.var(test['avg_cpu_usage'])
    ep=(svr_mse/va)*100
    errors['SVR'].append(ep)