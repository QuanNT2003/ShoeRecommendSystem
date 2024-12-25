# -*- coding: utf-8 -*-
"""ShoeRecommendation_BaseToReview.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1yKphIJ4nqqFf5KXDbjAFHdx925atlZYh
"""

# Cài đặt các thư viện cần thiết
!pip install pandas numpy scikit-learn
!pip install tensorflow-recommenders --no-deps
!pip install tensorflow==2.15.0
!pip install flask flask-cors pyngrok

# Import các thư viện cần thiết
import tensorflow as tf
import tensorflow_recommenders as tfrs
import pandas as pd
import numpy as np
import pickle
from sklearn.model_selection import train_test_split
from sklearn.metrics.pairwise import cosine_similarity
from tensorflow.keras.layers import StringLookup, Embedding
from flask import Flask, jsonify, request
from flask_cors import CORS
from pyngrok import ngrok

# Kết nối Google Drive
from google.colab import drive
drive.mount('/content/drive')

# Đọc dữ liệu sản phẩm và đánh giá
product_data = pd.read_csv('/content/drive/MyDrive/Data/products.csv')
reviews_data = pd.read_csv('/content/drive/MyDrive/Data/reviews.csv')

# Xử lý NaN
product_data = product_data.fillna('')
reviews_data = reviews_data.fillna('')

# Đảm bảo các cột có kiểu dữ liệu đồng nhất
product_data = product_data.astype(str)
reviews_data = reviews_data.astype(str)

user_ids = reviews_data["user"].unique().astype(str)
user_ids = np.array(user_ids)  # Đảm bảo user_ids là một mảng numpy
print("User IDs:", user_ids)

product_ids = product_data["productId"].unique().astype(str)
product_ids = np.array(product_ids)  # Đảm bảo product_ids là một mảng numpy
print("Product IDs:", product_ids)

brands = product_data["brand"].unique().astype(str)
categories = product_data["category"].unique().astype(str)
classifies = product_data["classify"].unique().astype(str)

# Tạo các đối tượng StringLookup cho userId và productId
user_id_lookup = StringLookup(vocabulary=user_ids, mask_token=None)
product_id_lookup = StringLookup(vocabulary=product_ids, mask_token=None)
brand_lookup = StringLookup(vocabulary=brands, mask_token=None)
category_lookup = StringLookup(vocabulary=categories, mask_token=None)
classify_lookup = StringLookup(vocabulary=classifies, mask_token=None)

# Chuyển đổi và mã hóa dữ liệu reviews_data
reviews_data["user_id_encoded"] = user_id_lookup(reviews_data["user"])
reviews_data["product_id_encoded"] = product_id_lookup(reviews_data["productId"])
reviews_data["rating"] = reviews_data["rating"].astype(float)  # Đảm bảo rating là số thực

# Ghép dữ liệu sản phẩm vào reviews_data theo productId
merged_data = reviews_data.merge(product_data, on="productId", how="left")
merged_data["brand_encoded"] = brand_lookup(merged_data["brand"])
merged_data["category_encoded"] = category_lookup(merged_data["category"])
merged_data["classify_encoded"] = classify_lookup(merged_data["classify"])

# Kiểm tra lại các cột mới mã hóa
print("\nEncoded Reviews Data Sample:")
print(reviews_data[["user", "user_id_encoded", "productId", "product_id_encoded" , "rating"]].apply)

# Tạo Dataset TensorFlow
train = tf.data.Dataset.from_tensor_slices({
    "user_id": tf.cast(merged_data["user_id_encoded"].values, tf.int32),
    "product_id": tf.cast(merged_data["product_id_encoded"].values, tf.int32),
    "brand": tf.cast(merged_data["brand_encoded"].values, tf.int32),
    "category": tf.cast(merged_data["category_encoded"].values, tf.int32),
    "classify": tf.cast(merged_data["classify_encoded"].values, tf.int32),
    "rating": tf.cast(merged_data["rating"].values, tf.float32)
}).batch(512)

test = tf.data.Dataset.from_tensor_slices({
    "user_id": tf.cast(merged_data["user_id_encoded"].values, tf.int32),
    "product_id": tf.cast(merged_data["product_id_encoded"].values, tf.int32),
    "brand": tf.cast(merged_data["brand_encoded"].values, tf.int32),
    "category": tf.cast(merged_data["category_encoded"].values, tf.int32),
    "classify": tf.cast(merged_data["classify_encoded"].values, tf.int32),
    "rating": tf.cast(merged_data["rating"].values, tf.float32)
}).batch(512).cache()

# Tạo Dataset cho product_ids
products_dataset = tf.data.Dataset.from_tensor_slices(product_ids).map(lambda x: tf.strings.as_string(x))

# Định nghĩa lớp mô hình khuyến nghị
class PersonalizedRecommendationModel(tfrs.Model):
    def __init__(self, use_factorized_top_k=True):
        super().__init__()
        embedding_dim = 32

        # Embedding cho từng trường
        self.user_embedding = tf.keras.Sequential([Embedding(len(user_ids) + 1, embedding_dim)])
        self.product_embedding = tf.keras.Sequential([Embedding(len(product_ids) + 1, embedding_dim)])

        # Khởi tạo embedding cho brand, category, và classify ngay cả khi không dùng FactorizedTopK
        self.brand_embedding = Embedding(len(brands) + 1, embedding_dim)
        self.category_embedding = Embedding(len(categories) + 1, embedding_dim)
        self.classify_embedding = Embedding(len(classifies) + 1, embedding_dim)

        # Chuyển các giá trị chuỗi thành số chỉ mục sử dụng StringLookup
        self.product_id_lookup = tf.keras.layers.StringLookup(vocabulary=product_ids, mask_token=None, oov_token="[UNK]")

        # Projection cuối
        self.final_projection = tf.keras.layers.Dense(embedding_dim)

        self.use_factorized_top_k = use_factorized_top_k
        if use_factorized_top_k:
            self.task = tfrs.tasks.Retrieval(
                metrics=tfrs.metrics.FactorizedTopK(
                    candidates=tf.data.Dataset.from_tensor_slices(
                        {"product_id": tf.constant(product_ids)}
                    ).batch(512).map(lambda x: self.product_embedding(self.product_id_lookup(x["product_id"])))
                )
            )
        else:
            self.task = tfrs.tasks.Retrieval()

    def call(self, inputs):
        user_emb = self.user_embedding(inputs["user_id"])
        product_emb = self.product_embedding(inputs["product_id"])
        brand_emb = self.brand_embedding(inputs["brand"])
        category_emb = self.category_embedding(inputs["category"])
        classify_emb = self.classify_embedding(inputs["classify"])

        # Kết hợp embedding
        combined_product_emb = tf.concat(
            [product_emb, brand_emb, category_emb, classify_emb], axis=-1
        )
        combined_product_emb = self.final_projection(combined_product_emb)

        return user_emb, combined_product_emb

    def compute_loss(self, features, training=False):
        user_emb, combined_product_emb = self(features)

        # Tích hợp rating làm trọng số trong tính toán loss
        weights = features["rating"]  # Dùng rating làm trọng số
        loss = self.task(user_emb, combined_product_emb, sample_weight=weights)

        return loss



# Hàm gợi ý sản phẩm cho người dùng
def recommend_products(user_id, num_recommendations=10):
    # Chuyển đổi user_id thành encoding
    user_encoded = user_id_lookup(tf.constant([str(user_id)]))

    # Lấy embedding của user
    user_emb = model.user_embedding(user_encoded)

    # Lấy embedding của sản phẩm (dùng product_embedding từ mô hình)
    product_embs = model.product_embedding(product_id_lookup(product_ids))

    # Tính toán độ tương thích giữa user và sản phẩm
    scores = tf.linalg.matmul(user_emb, tf.transpose(product_embs))

    # Lấy các sản phẩm có điểm số cao nhất
    recommended_product_ids = product_ids[np.argsort(scores.numpy()[0])[-num_recommendations:][::-1]]

    return recommended_product_ids.tolist()

# Huấn luyện mô hình
model = PersonalizedRecommendationModel()
model.compile(optimizer=tf.keras.optimizers.Adagrad(learning_rate=0.1))
model.fit(train, epochs=5)

# Đánh giá mô hình trên tập kiểm thử
test_results = model.evaluate(test, verbose=2)

# In ra loss và các chỉ số độ chính xác
print(f"Test Loss: {test_results[0]:.4f}")
print(f"Top 1 Categorical Accuracy: {test_results[1]:.4f}")
print(f"Top 5 Categorical Accuracy: {test_results[2]:.4f}")
print(f"Top 10 Categorical Accuracy: {test_results[3]:.4f}")
print(f"Top 50 Categorical Accuracy: {test_results[4]:.4f}")
print(f"Top 100 Categorical Accuracy: {test_results[5]:.4f}")

# Ví dụ gợi ý sản phẩm cho một người dùng
user_example = user_ids[0]
recommended_products = recommend_products(user_example)
print(f"Recommended Products for User '{user_example}': {recommended_products}")

# Đường dẫn đến các tệp dữ liệu
product_vectors_path = '/content/drive/MyDrive/Data/product_vectors.pkl'
product_metadata_path = '/content/drive/MyDrive/Data/product_metadata.csv'

# Load dữ liệu vector và metadata
with open(product_vectors_path, 'rb') as f:
    product_vectors = pickle.load(f)
product_data = pd.read_csv(product_metadata_path)

# Chuẩn hóa các vector để tăng hiệu suất
normalized_vectors = product_vectors / np.linalg.norm(product_vectors, axis=1, keepdims=True)

# Hàm tìm sản phẩm tương tự
def get_similar_products(product_id, top_k=3):
    if product_id not in product_data['productId'].values:
        return {"error": f"Product ID {product_id} not found"}

    product_idx = product_data[product_data['productId'] == product_id].index[0]
    product_vector = normalized_vectors[product_idx].reshape(1, -1)

    similarities = cosine_similarity(product_vector, normalized_vectors).flatten()
    similar_indices = np.argsort(-similarities)[1:top_k + 1]

    similar_products = product_data.iloc[similar_indices]
    return similar_products[['productId']].to_dict(orient='records')



# Flask API
app = Flask(__name__)
CORS(app)  # Cho phép truy cập từ mọi domain

@app.route('/recommend', methods=['GET'])
def recommend():
    user_id = request.args.get('user_id')  # Lấy user_id từ request
    num_recommendations = int(request.args.get('num_recommendations', 10))

    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    try:
        recommendations = recommend_products(user_id, num_recommendations)
        return jsonify({"user_id": user_id, "recommendations": recommendations})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/related-products', methods=['GET'])
def relatedproducts():
    product_id = request.args.get('product_id')
    top_k = int(request.args.get('top_k', 3))

    recommendations = get_similar_products(product_id, top_k=top_k)
    return jsonify(recommendations)

# Khởi động Flask API và ngrok
if __name__ == '__main__':
    # Thêm authtoken cho ngrok (chỉ cần chạy 1 lần, thay YOUR_AUTHTOKEN bằng mã của bạn)
    !ngrok config add-authtoken 2pTgSpvOuPpKFRz0cLysBPkWpZU_7RAQ9RfLEBfXMkSM4hzpr

    # Kết nối ngrok
    public_url = ngrok.connect(5000)
    print(f"Public URL: {public_url}")
    # Khởi chạy Flask
    app.run(host='0.0.0.0', port=5000)