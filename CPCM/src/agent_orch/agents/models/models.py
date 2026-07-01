from datetime import datetime
import pandas as pd
import numpy as np
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from prophet import Prophet
from xgboost import XGBRegressor
from sklearn.preprocessing import MinMaxScaler
from keras.models import Sequential
from keras.layers import Dense, LSTM
from sklearn.metrics import mean_squared_error
from sklearn.linear_model import LinearRegression, ElasticNet
from sklearn.ensemble import RandomForestRegressor
from sklearn.neighbors import KNeighborsRegressor
from sklearn.svm import SVR
from src.agent_orch.utils import DBConnect

# Function to prepare data for LSTM
def prepare_lstm_data(series, n_lags):
    X, y = [], []
    for i in range(n_lags, len(series)):
        X.append(series[i-n_lags:i])
        y.append(series[i])
    return np.array(X), np.array(y)

# Function to prepare data for XGBoost and other regressors
def create_lag_features(df, lag=1):
    if df.empty or len(df) < lag or 'avg_cpu_usage' not in df or 'date' not in df:
        return pd.DataFrame(columns=['date', 'avg_cpu_usage'] + [f'lag_{i}' for i in range(1, lag+1)])
    df_lagged = df[['date', 'avg_cpu_usage']].copy()  # Only keep relevant columns
    for i in range(1, lag+1):
        df_lagged[f'lag_{i}'] = df_lagged['avg_cpu_usage'].shift(i)
    return df_lagged.dropna()

def create_lag_features1(df, lag=1):
    if df.empty or len(df) < lag or 'avg_cpu_usage' not in df or 'date' not in df:
        return pd.DataFrame(columns=['date'] + [f'lag_{i}' for i in range(1, lag+1)]), pd.Series()
    df_lagged = df[['date', 'avg_cpu_usage']].copy()
    for i in range(1, lag + 1):
        df_lagged[f'lag_{i}'] = df_lagged['avg_cpu_usage'].shift(i)
    df_lagged = df_lagged.dropna()
    X_train = df_lagged[['date'] + [f'lag_{i}' for i in range(1, lag + 1)]]
    y_train = df_lagged['avg_cpu_usage']
    return X_train, y_train

def insert_db(in_data, run_id):
    in_data['yhat'] = in_data['yhat'].round(2)
    inserted_by = "ForecastingAgent"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db = DBConnect.DBConnect()
    for _, row in in_data.iterrows():
        record = {
            "run_id": run_id,
            "dop": row["ds"].strftime("%Y-%m-%d"),
            "alg": row["model"],
            "p_avg_usage": float(row["yhat"]),
            "inserted_by": inserted_by,
            "inserted_date": now,
            "updated_by": inserted_by,
            "updated_date": now
        }
        db.insert("forecast_results", record)
    db.close()

def ARIMA_model(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['ARIMA'].append(pd.DataFrame())
        errors['ARIMA'].append(float('inf'))
        return None
    arima_series = train['avg_cpu_usage']
    arima_model = ARIMA(arima_series, order=(5,1,0))
    arima_model_fit = arima_model.fit()
    arima_forecast = arima_model_fit.forecast(steps=days_predict)
    arima_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=pd.to_datetime(test['date']).min(), periods=days_predict, freq='D'),
        'yhat': arima_forecast,
        'model': ['ARIMA'] * days_predict,
        'server_id': [server_id] * days_predict
    })
    if not test.empty and 'avg_cpu_usage' in test:
        arima_mse = mean_squared_error(test['avg_cpu_usage'][:days_predict], arima_forecast[:len(test)])
        va = np.var(test['avg_cpu_usage'][:days_predict]) if not test['avg_cpu_usage'][:days_predict].empty else 1
        ep = (arima_mse / va) * 100 if va != 0 else float('inf')
        errors['ARIMA'].append(ep)
    else:
        errors['ARIMA'].append(float('inf'))
    forecasts['ARIMA'].append(arima_forecast_df)
    return arima_model_fit

def SARIMA_model(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['SARIMA'].append(pd.DataFrame())
        errors['SARIMA'].append(float('inf'))
        return None
    arima_series = train['avg_cpu_usage']
    print(test)
    sarima_model = SARIMAX(arima_series, order=(1, 1, 1), seasonal_order=(1, 1, 1, 12))
    sarima_model_fit = sarima_model.fit(disp=False)
    sarima_forecast = sarima_model_fit.forecast(steps=days_predict)
    sarima_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=pd.to_datetime(test['date']).min(), periods=days_predict, freq='D'),
        'yhat': sarima_forecast,
        'model': ['SARIMA'] * days_predict,
        'server_id': [server_id] * days_predict
    })
    if not test.empty and 'avg_cpu_usage' in test:
        sarima_mse = mean_squared_error(test['avg_cpu_usage'][:days_predict], sarima_forecast[:len(test)])
        va = np.var(test['avg_cpu_usage'][:days_predict]) if not test['avg_cpu_usage'][:days_predict].empty else 1
        ep = (sarima_mse / va) * 100 if va != 0 else float('inf')
        errors['SARIMA'].append(ep)
    else:
        errors['SARIMA'].append(float('inf'))
    print(sarima_forecast_df)
    forecasts['SARIMA'].append(sarima_forecast_df)
    return sarima_model_fit

def Holt_Winters_model(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['Holt-Winters'].append(pd.DataFrame())
        errors['Holt-Winters'].append(float('inf'))
        return None
    arima_series = train['avg_cpu_usage']
    holt_model = ExponentialSmoothing(arima_series, seasonal='add', seasonal_periods=12)
    holt_model_fit = holt_model.fit()
    holt_forecast = holt_model_fit.forecast(steps=days_predict)
    holt_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=pd.to_datetime(test['date']).min(), periods=days_predict, freq='D'),
        'yhat': holt_forecast,
        'model': ['Holt-Winters'] * days_predict,
        'server_id': [server_id] * days_predict
    })
    if not test.empty and 'avg_cpu_usage' in test:
        holt_mse = mean_squared_error(test['avg_cpu_usage'][:days_predict], holt_forecast[:len(test)])
        va = np.var(test['avg_cpu_usage'][:days_predict]) if not test['avg_cpu_usage'][:days_predict].empty else 1
        ep = (holt_mse / va) * 100 if va != 0 else float('inf')
        errors['Holt-Winters'].append(ep)
    else:
        errors['Holt-Winters'].append(float('inf'))
    forecasts['Holt-Winters'].append(holt_forecast_df)
    return holt_model

def RFR_model(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or test.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['Random Forest'].append(pd.DataFrame())
        errors['Random Forest'].append(float('inf'))
        return None
    xgb_train = create_lag_features(train, lag=5)
    if xgb_train.empty:
        forecasts['Random Forest'].append(pd.DataFrame())
        errors['Random Forest'].append(float('inf'))
        return None
    X_train = xgb_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = xgb_train['avg_cpu_usage']
    xgb_test = create_lag_features(test, lag=5)
    if xgb_test.empty:
        forecasts['Random Forest'].append(pd.DataFrame())
        errors['Random Forest'].append(float('inf'))
        return None
    X_test = xgb_test.drop(columns=['date', 'avg_cpu_usage'])
    n = min(len(X_test), days_predict)
    rf_model = RandomForestRegressor(n_estimators=100)
    rf_model.fit(X_train, y_train)
    rf_forecast = rf_model.predict(X_test)[:n]
    rf_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=pd.to_datetime(test['date']).min(), periods=n, freq='D'),
        'yhat': rf_forecast,
        'model': ['Random Forest'] * n,
        'server_id': [server_id] * n
    })
    if not test.empty and 'avg_cpu_usage' in test:
        rf_mse = mean_squared_error(test['avg_cpu_usage'][:n], rf_forecast)
        va = np.var(test['avg_cpu_usage'][:n]) if not test['avg_cpu_usage'][:n].empty else 1
        ep = (rf_mse / va) * 100 if va != 0 else float('inf')
        errors['Random Forest'].append(ep)
    else:
        errors['Random Forest'].append(float('inf'))
    forecasts['Random Forest'].append(rf_forecast_df)
    return rf_model

def RFR_model1(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or test.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['Random Forest'].append(pd.DataFrame())
        errors['Random Forest'].append(float('inf'))
        return None
    xgb_train = create_lag_features(train, lag=5)
    if xgb_train.empty:
        forecasts['Random Forest'].append(pd.DataFrame())
        errors['Random Forest'].append(float('inf'))
        return None
    X_train = xgb_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = xgb_train['avg_cpu_usage']
    xgb_test = create_lag_features(test, lag=5)
    if xgb_test.empty:
        forecasts['Random Forest'].append(pd.DataFrame())
        errors['Random Forest'].append(float('inf'))
        return None
    X_test = xgb_test.drop(columns=['date', 'avg_cpu_usage'])
    n = min(len(X_test), days_predict)
    rf_model = RandomForestRegressor(n_estimators=100, random_state=42)
    rf_model.fit(X_train, y_train)
    rf_forecast = rf_model.predict(X_test)[:n]
    rf_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=pd.to_datetime(test['date']).min(), periods=n, freq='D'),
        'yhat': rf_forecast,
        'model': ['Random Forest'] * n,
        'server_id': [server_id] * n
    })
    if not test.empty and 'avg_cpu_usage' in test:
        rf_mse = mean_squared_error(test['avg_cpu_usage'][:n], rf_forecast)
        va = np.var(test['avg_cpu_usage'][:n]) if not test['avg_cpu_usage'][:n].empty else 1
        ep = (rf_mse / va) * 100 if va != 0 else float('inf')
        errors['Random Forest'].append(ep)
    else:
        errors['Random Forest'].append(float('inf'))
    forecasts['Random Forest'].append(rf_forecast_df)
    return rf_model

def Prophet_Model(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['Prophet'].append(pd.DataFrame())
        errors['Prophet'].append(float('inf'))
        return None
    prophet_df = train[['date', 'avg_cpu_usage']].rename(columns={'date': 'ds', 'avg_cpu_usage': 'y'})
    prophet_model = Prophet()
    prophet_model.fit(prophet_df)
    prophet_future = prophet_model.make_future_dataframe(periods=days_predict)
    prophet_forecast = prophet_model.predict(prophet_future).tail(days_predict)
    prophet_forecast_df = prophet_forecast[['ds', 'yhat']].copy()
    prophet_forecast_df['model'] = ['Prophet'] * len(prophet_forecast_df)
    prophet_forecast_df['server_id'] = [server_id] * len(prophet_forecast_df)
    if not test.empty and 'avg_cpu_usage' in test:
        prophet_mse = mean_squared_error(test['avg_cpu_usage'][:len(prophet_forecast)], prophet_forecast['yhat'])
        va = np.var(test['avg_cpu_usage'][:len(prophet_forecast)]) if not test['avg_cpu_usage'][:len(prophet_forecast)].empty else 1
        ep = (prophet_mse / va) * 100 if va != 0 else float('inf')
        errors['Prophet'].append(ep)
    else:
        errors['Prophet'].append(float('inf'))
    forecasts['Prophet'].append(prophet_forecast_df)
    return prophet_model

def XGBoost_Model(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or test.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['XGBoost'].append(pd.DataFrame())
        errors['XGBoost'].append(float('inf'))
        return None
    xgb_train = create_lag_features(train, lag=5)
    if xgb_train.empty:
        forecasts['XGBoost'].append(pd.DataFrame())
        errors['XGBoost'].append(float('inf'))
        return None
    X_train = xgb_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = xgb_train['avg_cpu_usage']
    xgb_test = create_lag_features(test, lag=5)
    if xgb_test.empty:
        forecasts['XGBoost'].append(pd.DataFrame())
        errors['XGBoost'].append(float('inf'))
        return None
    X_test = xgb_test.drop(columns=['date', 'avg_cpu_usage'])
    n = min(len(X_test), days_predict)
    xgb_model = XGBRegressor()
    xgb_model.fit(X_train, y_train)
    xgb_forecast = xgb_model.predict(X_test)[:n]
    xgb_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=pd.to_datetime(test['date']).min(), periods=n, freq='D'),
        'yhat': xgb_forecast,
        'model': ['XGBoost'] * n,
        'server_id': [server_id] * n
    })
    if not test.empty and 'avg_cpu_usage' in test:
        actuals = test['avg_cpu_usage'].values[:n]
        xgb_mse = mean_squared_error(actuals, xgb_forecast)
        va = np.var(actuals) if not actuals.size == 0 else 1
        ep = (xgb_mse / va) * 100 if va != 0 else float('inf')
        errors['XGBoost'].append(ep)
    else:
        errors['XGBoost'].append(float('inf'))
    forecasts['XGBoost'].append(xgb_forecast_df)
    return xgb_model

def LSTM_Model(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['LSTM'].append(pd.DataFrame())
        errors['LSTM'].append(float('inf'))
        return None
    lstm_series = train['avg_cpu_usage'].values
    n_lags = 10
    if len(lstm_series) < n_lags:
        forecasts['LSTM'].append(pd.DataFrame())
        errors['LSTM'].append(float('inf'))
        return None
    X, y = prepare_lstm_data(lstm_series, n_lags)
    lstm_model = Sequential([
        LSTM(50, activation='relu', input_shape=(n_lags, 1)),
        Dense(1)
    ])
    lstm_model.compile(optimizer='adam', loss='mse')
    lstm_model.fit(X, y, epochs=50, verbose=0)
    lstm_input = lstm_series[-n_lags:]
    lstm_predictions = []
    for _ in range(days_predict):
        lstm_input_reshaped = lstm_input.reshape((1, n_lags, 1))
        prediction = lstm_model.predict(lstm_input_reshaped, verbose=0)
        lstm_predictions.append(prediction[0][0])
        lstm_input = np.append(lstm_input[1:], prediction)
    lstm_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=pd.to_datetime(test['date']).min(), periods=days_predict, freq='D'),
        'yhat': lstm_predictions,
        'model': ['LSTM'] * days_predict,
        'server_id': [server_id] * days_predict
    })
    if not test.empty and 'avg_cpu_usage' in test:
        lstm_mse = mean_squared_error(test['avg_cpu_usage'][:days_predict], lstm_predictions)
        va = np.var(test['avg_cpu_usage'][:days_predict]) if not test['avg_cpu_usage'][:days_predict].empty else 1
        ep = (lstm_mse / va) * 100 if va != 0 else float('inf')
        errors['LSTM'].append(ep)
    else:
        errors['LSTM'].append(float('inf'))
    forecasts['LSTM'].append(lstm_forecast_df)
    return lstm_model

def LR_Model(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or test.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['Linear Regression'].append(pd.DataFrame())
        errors['Linear Regression'].append(float('inf'))
        return None
    lr_train = create_lag_features(train, lag=5)
    if lr_train.empty:
        forecasts['Linear Regression'].append(pd.DataFrame())
        errors['Linear Regression'].append(float('inf'))
        return None
    X_train = lr_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = lr_train['avg_cpu_usage']
    lr_test = create_lag_features(test, lag=5)
    if lr_test.empty:
        forecasts['Linear Regression'].append(pd.DataFrame())
        errors['Linear Regression'].append(float('inf'))
        return None
    X_test = lr_test.drop(columns=['date', 'avg_cpu_usage'])
    n = min(len(X_test), days_predict)
    lr_model = LinearRegression()
    lr_model.fit(X_train, y_train)
    lr_forecast = lr_model.predict(X_test)[:n]
    lr_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=pd.to_datetime(test['date']).min(), periods=n, freq='D'),
        'yhat': lr_forecast,
        'model': ['Linear Regression'] * n,
        'server_id': [server_id] * n
    })
    if not test.empty and 'avg_cpu_usage' in test:
        lr_mse = mean_squared_error(test['avg_cpu_usage'][:n], lr_forecast)
        va = np.var(test['avg_cpu_usage'][:n]) if not test['avg_cpu_usage'][:n].empty else 1
        ep = (lr_mse / va) * 100 if va != 0 else float('inf')
        errors['Linear Regression'].append(ep)
    else:
        errors['Linear Regression'].append(float('inf'))
    forecasts['Linear Regression'].append(lr_forecast_df)
    return lr_model

def KNN_Model(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or test.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['KNN'].append(pd.DataFrame())
        errors['KNN'].append(float('inf'))
        return None
    xgb_train = create_lag_features(train, lag=5)
    if xgb_train.empty:
        forecasts['KNN'].append(pd.DataFrame())
        errors['KNN'].append(float('inf'))
        return None
    X_train = xgb_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = xgb_train['avg_cpu_usage']
    xgb_test = create_lag_features(test, lag=5)
    if xgb_test.empty:
        forecasts['KNN'].append(pd.DataFrame())
        errors['KNN'].append(float('inf'))
        return None
    X_test = xgb_test.drop(columns=['date', 'avg_cpu_usage'])
    # Ensure feature names match
    if set(X_train.columns) != set(X_test.columns):
        missing_cols = set(X_train.columns) - set(X_test.columns)
        extra_cols = set(X_test.columns) - set(X_train.columns)
        raise ValueError(f"Feature mismatch: Missing {missing_cols}, Extra {extra_cols}")
    n = min(len(X_test), days_predict)
    knn_model = KNeighborsRegressor(n_neighbors=5)
    knn_model.fit(X_train, y_train)
    knn_forecast = knn_model.predict(X_test)[:n]
    knn_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=pd.to_datetime(test['date']).min(), periods=n, freq='D'),
        'yhat': knn_forecast,
        'model': ['KNN'] * n,
        'server_id': [server_id] * n
    })
    if not test.empty and 'avg_cpu_usage' in test:
        knn_mse = mean_squared_error(test['avg_cpu_usage'][:n], knn_forecast)
        va = np.var(test['avg_cpu_usage'][:n]) if not test['avg_cpu_usage'][:n].empty else 1
        ep = (knn_mse / va) * 100 if va != 0 else float('inf')
        errors['KNN'].append(ep)
    else:
        errors['KNN'].append(float('inf'))
    forecasts['KNN'].append(knn_forecast_df)
    return knn_model

def EN_Model(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or test.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['Elastic Net'].append(pd.DataFrame())
        errors['Elastic Net'].append(float('inf'))
        return None
    xgb_train = create_lag_features(train, lag=5)
    if xgb_train.empty:
        forecasts['Elastic Net'].append(pd.DataFrame())
        errors['Elastic Net'].append(float('inf'))
        return None
    X_train = xgb_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = xgb_train['avg_cpu_usage']
    xgb_test = create_lag_features(test, lag=5)
    if xgb_test.empty:
        forecasts['Elastic Net'].append(pd.DataFrame())
        errors['Elastic Net'].append(float('inf'))
        return None
    X_test = xgb_test.drop(columns=['date', 'avg_cpu_usage'])
    n = min(len(X_test), days_predict)
    en_model = ElasticNet()
    en_model.fit(X_train, y_train)
    en_forecast = en_model.predict(X_test)[:n]
    en_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=pd.to_datetime(test['date']).min(), periods=n, freq='D'),
        'yhat': en_forecast,
        'model': ['Elastic Net'] * n,
        'server_id': [server_id] * n
    })
    if not test.empty and 'avg_cpu_usage' in test:
        en_mse = mean_squared_error(test['avg_cpu_usage'][:n], en_forecast)
        va = np.var(test['avg_cpu_usage'][:n]) if not test['avg_cpu_usage'][:n].empty else 1
        ep = (en_mse / va) * 100 if va != 0 else float('inf')
        errors['Elastic Net'].append(ep)
    else:
        errors['Elastic Net'].append(float('inf'))
    forecasts['Elastic Net'].append(en_forecast_df)
    return en_model

def SVR_Model(train, test, days_predict, forecasts, errors, server_id, r_type, table):
    if train.empty or test.empty or 'avg_cpu_usage' not in train or not days_predict > 0:
        forecasts['SVR'].append(pd.DataFrame())
        errors['SVR'].append(float('inf'))
        return None
    xgb_train = create_lag_features(train, lag=5)
    if xgb_train.empty:
        forecasts['SVR'].append(pd.DataFrame())
        errors['SVR'].append(float('inf'))
        return None
    X_train = xgb_train.drop(columns=['date', 'avg_cpu_usage'])
    y_train = xgb_train['avg_cpu_usage']
    xgb_test = create_lag_features(test, lag=5)
    if xgb_test.empty:
        forecasts['SVR'].append(pd.DataFrame())
        errors['SVR'].append(float('inf'))
        return None
    X_test = xgb_test.drop(columns=['date', 'avg_cpu_usage'])
    n = min(len(X_test), days_predict)
    svr_model = SVR()
    svr_model.fit(X_train, y_train)
    svr_forecast = svr_model.predict(X_test)[:n]
    svr_forecast_df = pd.DataFrame({
        'ds': pd.date_range(start=pd.to_datetime(test['date']).min(), periods=n, freq='D'),
        'yhat': svr_forecast,
        'model': ['SVR'] * n,
        'server_id': [server_id] * n
    })
    if not test.empty and 'avg_cpu_usage' in test:
        svr_mse = mean_squared_error(test['avg_cpu_usage'][:n], svr_forecast)
        va = np.var(test['avg_cpu_usage'][:n]) if not test['avg_cpu_usage'][:n].empty else 1
        ep = (svr_mse / va) * 100 if va != 0 else float('inf')
        errors['SVR'].append(ep)
    else:
        errors['SVR'].append(float('inf'))
    forecasts['SVR'].append(svr_forecast_df)
    return svr_model