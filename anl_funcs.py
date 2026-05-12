import pandas as pd
import numpy as np
from typing import TypedDict, List, Dict, Any, Optional, Tuple


# =============================================================================
# SPECIALIZED ANALYSIS FUNCTIONS
# =============================================================================

class SpecializedAnalytics:
    """Collection of specialized analysis functions for different data types and scenarios"""

    @staticmethod
    def time_series_decomposition(df: pd.DataFrame, date_col: str, value_col: str) -> Dict[str, Any]:
        """Advanced time series analysis with trend, seasonal, and residual decomposition"""
        try:
            from statsmodels.tsa.seasonal import seasonal_decompose

            ts_df = df.copy()
            # Parse dates, drop invalid
            ts_df[date_col] = pd.to_datetime(ts_df[date_col], errors='coerce')
            ts_df = ts_df.dropna(subset=[date_col, value_col])
            ts_df = ts_df.sort_values(date_col).set_index(date_col)

            # Resample and fill missing
            freq = 'D' if len(ts_df) > 365 else 'M'
            ts_resampled = ts_df[value_col].resample(freq).mean().ffill().bfill()
            min_required = 60 if freq == 'D' else 24

            if len(ts_resampled) < min_required:
                return {
                    'observed': ts_resampled.to_dict(),
                    'summary': f'Insufficient observations for seasonal decomposition. Needed at least {min_required}, found {len(ts_resampled)}.',
                    'analysis_type': 'time_series_decomposition'
                }

            decomposition = seasonal_decompose(ts_resampled, model='additive', period=30 if freq=='D' else 12)

            return {
                'trend': decomposition.trend.dropna().to_dict(),
                'seasonal': decomposition.seasonal.dropna().to_dict(),
                'residual': decomposition.resid.dropna().to_dict(),
                'observed': decomposition.observed.to_dict(),
                'analysis_type': 'time_series_decomposition'
            }
        except Exception as e:
            return {'error': f"Time series decomposition failed: {str(e)}"}

    @staticmethod
    def cohort_analysis(df: pd.DataFrame, customer_col: str, date_col: str, value_col: str=None) -> Dict[str, Any]:
        try:
            cohort_df = df.copy()

            # Ensure datetime
            cohort_df[date_col] = pd.to_datetime(cohort_df[date_col], errors='coerce')
            cohort_df = cohort_df.dropna(subset=[date_col, customer_col])

            # Check if we have enough data after cleaning
            if len(cohort_df) < 2:
                return {'error': 'Cohort analysis failed: Insufficient data after cleaning (need at least 2 records)'}

            # Order period (YYYY-MM)
            cohort_df['order_period'] = cohort_df[date_col].dt.to_period('M')

            # Cohort group = first purchase month
            first_period = cohort_df.groupby(customer_col)[date_col].transform('min').dt.to_period('M')
            cohort_df['cohort_group'] = first_period

            # Period number (months since cohort start)
            cohort_df['period_number'] = (
                (cohort_df['order_period'].dt.year - cohort_df['cohort_group'].dt.year) * 12 +
                (cohort_df['order_period'].dt.month - cohort_df['cohort_group'].dt.month)
            )

            # Cohort counts
            cohort_data = (
                cohort_df.groupby(['cohort_group', 'period_number'])[customer_col]
                        .nunique()
                        .reset_index(name='user_count')
            )

            # Check if cohort_data is empty
            if len(cohort_data) == 0:
                return {'error': 'Cohort analysis failed: No cohort data generated'}

            cohort_counts = cohort_data.pivot(index='cohort_group',
                                            columns='period_number',
                                            values='user_count')

            # Validate pivot result
            if cohort_counts.empty or cohort_counts.shape[1] == 0:
                return {'error': 'Cohort analysis failed: Pivot table is empty or has no columns'}

            # Clean NaN indices
            cohort_counts = cohort_counts[~cohort_counts.index.isna()]
            
            if cohort_counts.empty:
                return {'error': 'Cohort analysis failed: No valid cohort groups after cleaning'}

            # Convert index to string for serialization
            cohort_counts.index = cohort_counts.index.astype(str)

            # Get cohort sizes - use the period_number = 0 column if it exists, otherwise first column
            if 0 in cohort_counts.columns:
                cohort_sizes = cohort_counts[0]  # Period 0 = first month
            elif cohort_counts.shape[1] > 0:
                cohort_sizes = cohort_counts.iloc[:, 0]  # First available column
            else:
                return {'error': 'Cohort analysis failed: No columns available for cohort sizes'}

            # Ensure cohort_sizes alignment
            cohort_sizes.index = cohort_counts.index

            # Handle case where cohort_sizes has zeros (avoid division by zero)
            if (cohort_sizes == 0).any():
                print("Warning: Some cohort groups have zero initial size")
                cohort_sizes = cohort_sizes.replace(0, np.nan)

            # Calculate retention rates
            retention = cohort_counts.divide(cohort_sizes, axis=0)

            # Additional validation
            if retention.empty:
                return {'error': 'Cohort analysis failed: Empty retention table'}

            return {
                'cohort_counts': cohort_counts.to_dict(),
                'retention_rates': retention.to_dict(),
                'cohort_sizes': cohort_sizes.to_dict(),
                'analysis_type': 'cohort_analysis'
            }

        except Exception as e:
            return {'error': f"Cohort analysis failed: {e}"}

    @staticmethod
    def customer_segmentation(df: pd.DataFrame, customer_col: str, features: List[str]) -> Dict[str, Any]:
        """RFM analysis and customer segmentation"""
        print(df, customer_col, features)
        try:
            from sklearn.cluster import KMeans
            from sklearn.preprocessing import StandardScaler

            # Filter to numeric features
            numeric_feats = [col for col in features if col in df.columns and pd.api.types.is_numeric_dtype(df[col])]
            if not numeric_feats:
                return {'error': "Customer segmentation failed: No numeric features provided"}

            # Aggregate customer metrics
            cust = (
                df.groupby(customer_col)[numeric_feats]
                .agg(['mean', 'sum', 'count'])
                .dropna()
            )
            cust.columns = ['_'.join(col).strip() for col in cust.columns.values]
            cust = cust.reset_index()

            feature_cols = [c for c in cust.columns if c != customer_col]
            scaler = StandardScaler()
            scaled = scaler.fit_transform(cust[feature_cols])

            kmeans = KMeans(n_clusters=5, random_state=42)
            cust['cluster'] = kmeans.fit_predict(scaled)

            segment_summary = cust.groupby('cluster')[feature_cols].mean().round(2)

            return {
                'customer_segments': cust.to_dict('records'),
                'segment_summary': segment_summary.to_dict(),
                'cluster_centers': kmeans.cluster_centers_.tolist(),
                'analysis_type': 'customer_segmentation'
            }
        except Exception as e:
            return {'error': f"Customer segmentation failed: {str(e)}"}

        
    @staticmethod
    def correlation_network_analysis(df: pd.DataFrame, numeric_columns: List[str], threshold: float = 0.5) -> Dict[str, Any]:
        """Advanced correlation analysis with network visualization data"""
        try:
            # Calculate correlation matrix
            corr_matrix = df[numeric_columns].corr()
            
            # Find strong correlations
            strong_correlations = []
            for i in range(len(corr_matrix.columns)):
                for j in range(i+1, len(corr_matrix.columns)):
                    corr_value = corr_matrix.iloc[i, j]
                    if abs(corr_value) > threshold:
                        strong_correlations.append({
                            'source': corr_matrix.columns[i],
                            'target': corr_matrix.columns[j],
                            'correlation': float(corr_value),
                            'strength': abs(corr_value)
                        })
            
            # Network metrics
            node_metrics = {}
            for col in numeric_columns:
                connections = sum(1 for corr in strong_correlations if corr['source'] == col or corr['target'] == col)
                avg_correlation = np.mean([abs(corr['correlation']) for corr in strong_correlations 
                                        if corr['source'] == col or corr['target'] == col])
                node_metrics[col] = {
                    'connections': connections,
                    'avg_correlation': float(avg_correlation) if not np.isnan(avg_correlation) else 0
                }
            
            return {
                'correlation_matrix': corr_matrix.to_dict(),
                'strong_correlations': strong_correlations,
                'node_metrics': node_metrics,
                'analysis_type': 'correlation_network'
            }
        except Exception as e:
            return {'error': f"Correlation network analysis failed: {str(e)}"}
        
    @staticmethod
    def anomaly_detection(df: pd.DataFrame, columns: List[str], method: str = 'isolation_forest') -> Dict[str, Any]:
        """Advanced anomaly detection using multiple algorithms"""
        try:
            from sklearn.ensemble import IsolationForest
            from sklearn.preprocessing import StandardScaler
            
            # Prepare data
            analysis_df = df[columns].dropna()
            scaler = StandardScaler()
            scaled_data = scaler.fit_transform(analysis_df)
            
            # Isolation Forest for anomaly detection
            iso_forest = IsolationForest(contamination=0.1, random_state=42)
            anomalies = iso_forest.fit_predict(scaled_data)
            
            # Mark anomalies
            analysis_df = analysis_df.copy()
            analysis_df['anomaly'] = anomalies
            analysis_df['anomaly_score'] = iso_forest.decision_function(scaled_data)
            
            # Anomaly statistics
            anomaly_stats = {
                'total_anomalies': int((anomalies == -1).sum()),
                'anomaly_percentage': float((anomalies == -1).mean() * 100),
                'columns_analyzed': columns
            }
            
            # Top anomalies
            top_anomalies = analysis_df[analysis_df['anomaly'] == -1].nsmallest(10, 'anomaly_score')
            
            return {
                'anomaly_stats': anomaly_stats,
                'anomalies': analysis_df[analysis_df['anomaly'] == -1].to_dict('records'),
                'top_anomalies': top_anomalies.to_dict('records'),
                'analysis_type': 'anomaly_detection'
            }
        except Exception as e:
            return {'error': f"Anomaly detection failed: {str(e)}"}
        
    @staticmethod
    def distribution_comparison(df: pd.DataFrame, target_col: str, group_col: str) -> Dict[str, Any]:
        """Statistical comparison of distributions across groups"""
        try:
            from scipy import stats
            
            groups = df[group_col].unique()
            group_data = {group: df[df[group_col] == group][target_col].dropna() for group in groups}
            
            # Statistical tests
            results = {}
            
            # Shapiro-Wilk test for normality
            normality_tests = {}
            for group, data in group_data.items():
                if len(data) > 3:  # Need at least 3 observations
                    stat, p_value = stats.shapiro(data[:5000])  # Limit for large datasets
                    normality_tests[str(group)] = {'statistic': float(stat), 'p_value': float(p_value)}
            
            # ANOVA or Kruskal-Wallis test
            group_values = list(group_data.values())
            if len(group_values) >= 2:
                try:
                    # ANOVA (parametric)
                    f_stat, p_value = stats.f_oneway(*group_values)
                    results['anova'] = {'f_statistic': float(f_stat), 'p_value': float(p_value)}
                    
                    # Kruskal-Wallis (non-parametric)
                    h_stat, p_value = stats.kruskal(*group_values)
                    results['kruskal_wallis'] = {'h_statistic': float(h_stat), 'p_value': float(p_value)}
                except:
                    pass
            
            # Descriptive statistics by group
            group_stats = {}
            for group, data in group_data.items():
                group_stats[str(group)] = {
                    'mean': float(data.mean()),
                    'median': float(data.median()),
                    'std': float(data.std()),
                    'count': int(len(data))
                }
            
            return {
                'group_statistics': group_stats,
                'normality_tests': normality_tests,
                'statistical_tests': results,
                'analysis_type': 'distribution_comparison'
            }
        except Exception as e:
            return {'error': f"Distribution comparison failed: {str(e)}"}
