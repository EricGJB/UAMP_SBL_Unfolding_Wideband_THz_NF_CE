import numpy as np
random_seed = 2023
np.random.seed(random_seed)
from matplotlib import pyplot as plt
from scipy import io
from functions import C2R,complex_matrix_multiplication,Fixed_Phi_Layer_FR_dynamic_Mr,A_R_Layer_FR

# use partial dataset 
test_num = 10000
Nr = 256
num_sc = 32 # number of subcarriers
fc = 100 * 1e9 # central frequency
fs = 10 * 1e9 # bandwidth
eta = fs / num_sc  # subcarrier spacing
c = 3e8
lamda = c/fc
d = lamda/2 # half wavelength

s = 2
G_angle = s*Nr # angular resolution

num_clusters = 3

channel_model = 'cluster'
channelset_name = 'channel_%s_%dscs.mat'%(channel_model,num_sc)
H_list = io.loadmat('./data/'+channelset_name)['H_list']
H_list = H_list[:test_num]
H_list = np.transpose(H_list,(0,2,1))
H_real_imag_list = C2R(H_list)

H_real_imag_list = np.reshape(H_real_imag_list,(-1,Nr*num_sc*2))

print(H_real_imag_list.shape)

SNR_collection = [0, 5, 10, 15, 20]
Mr_collection = [32, 40, 48, 56, 64]

Mr_max = Mr_collection[-1]

def polar_domain_manifold(Nt, d, f, r0, theta0):
    c = 3e8
    nn = np.arange(-(Nt-1)/2, (Nt-1)/2+1)
    r = r0 - nn * d * np.sin(theta0) + nn**2 * d**2 * np.cos(theta0)**2 / 2 /r0
    at = np.exp(-1j*2*np.pi*f*(r-r0)/c)/np.sqrt(Nt)
    return np.expand_dims(at,axis=-1)

def dictionary_polar(N, d, lamda, G_angle, fn, beta, rho_min):
    dictionary = []

    theta = np.linspace(-1 + 1 / G_angle, 1 - 1 / G_angle, G_angle)
    
    # far-field atoms 
    rr = 1e5
    for idx in range(G_angle):    
        dictionary.append(polar_domain_manifold(N, d, fn, rr, np.arcsin(theta[idx])))   
    
    # near-field atoms 
    Z = (N*d)**2 / 2 / lamda / beta**2
    s = 1
    while Z/s>=rho_min:
        for idx in range(G_angle):    
            rr = Z/s*(1-theta[idx]**2)
            dictionary.append(polar_domain_manifold(N, d, fn, rr, np.arcsin(theta[idx])))    
        s = s + 1
            
    dictionary = np.concatenate(dictionary,axis=-1)
    
    return dictionary

A_list_polar = []

beta = 1.2

rho_min = 3

A_n_FID = dictionary_polar(Nr, d, lamda, G_angle, fc, beta, rho_min)

G_polar = A_n_FID.shape[-1]
print(G_polar)

for n in range(num_sc):
    fn = fc + (n-(num_sc-1)/2)*eta
    A_n = dictionary_polar(Nr, d, lamda, G_angle, fn, beta, rho_min)
    A_list_polar.append(A_n)    

W_dict = {}
U_list_dict = {}
Phi_list_polar_dict = {}

for Mr in Mr_collection:
    # the receive combining matrix, consists of random binary phases
    W = np.random.choice([1, -1], Nr * Mr, replace=True) / np.sqrt(Nr)
    W = np.reshape(W, (Nr, Mr))
    W = np.matrix(W)
    W_dict['%s'%Mr] = W
    
    U_list = np.zeros((num_sc, Mr, Mr),dtype=np.complex64)
    Phi_list_polar = np.zeros((num_sc, Mr, G_polar),dtype=np.complex64)
    
    for i in range(num_sc):
        Phi_polar = W.H.dot(A_list_polar[i])
        U,Sigma,V = np.linalg.svd(Phi_polar)
        Phi_polar = np.transpose(np.conjugate(U)).dot(Phi_polar)
        U_list[i] = U
        Phi_list_polar[i] = Phi_polar

    U_list_dict['%s'%Mr] = U_list
    # padding for dimension consistency
    Phi_list_polar = np.concatenate([np.zeros((num_sc,Mr_max-Mr,G_polar),dtype=np.complex64),Phi_list_polar],axis=1)
    Phi_list_polar_dict['%s'%Mr] = Phi_list_polar


Y_list = np.zeros((test_num,Mr_max,num_sc),dtype=np.complex64)

Mr_list = np.zeros((test_num,1))
SNR_list = np.zeros((test_num,1))
loss_weight_list = np.zeros((test_num,1))

nmse_matrix = np.array([[0.6405,0.4644,0.3492,0.2885,0.2629],[0.5514,0.3757,0.2610,0.1997,0.1712],[0.5055,0.3227,0.2075,0.1434,0.1117],[0.4613,0.2787,0.1647,0.1025,0.0714],[0.4175,0.2438,0.1361,0.0765,0.0458]])
loss_weight_matrix = nmse_matrix[2,2]/nmse_matrix

for i in range(test_num):
    Mr = np.random.choice(Mr_collection) 
    Mr_list[i] = Mr
    
    SNR = np.random.choice(SNR_collection)
    SNR_list[i] = SNR

    loss_weight = loss_weight_matrix[Mr//8-4][SNR//5]
    loss_weight_list[i] = loss_weight

    W = W_dict['%s'%Mr]
    Phi_list_polar = Phi_list_polar_dict['%s'%Mr]
    U_list = U_list_dict['%s'%Mr]
    
    sigma_2 = 1 / 10 ** (SNR / 10)  # noise variance
    noises = np.sqrt(sigma_2/2)*(np.random.randn(Mr,num_sc)+1j*np.random.randn(Mr,num_sc))

    Y = W.H.dot(H_list[i])+noises
    Y = np.array(Y)

    for j in range(num_sc):
        Y_list[i,Mr_max-Mr:,j:j+1] = np.transpose(np.conjugate(U_list[j])).dot(Y[:,j:j+1])

Y_real_imag_list = C2R(Y_list)

print(Y_real_imag_list.shape)

print(loss_weight_list.shape)

#%% DNN
import tensorflow as tf
tf.random.set_seed(2023)

from tensorflow.keras.models import Model
from tensorflow.keras.layers import Input,Conv1D,Conv2D,Lambda,AveragePooling1D,Dense
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import ReduceLROnPlateau, EarlyStopping, ModelCheckpoint

use_gpu = 1

if use_gpu:
    import os
    os.environ["CUDA_VISIBLE_DEVICES"]='2'
    gpus = tf.config.experimental.list_physical_devices('GPU')
    tf.config.experimental.set_memory_growth(gpus[0],True)


#%% 
def update_UAMP_SBL(Phi_real_imag_list, y_real_imag_list, Tau_x_list, X_hat_real_imag_list, alpha_hat_list, S_real_imag_list, beta):
    Tau_x_list_new = []
    X_hat_real_imag_list_new = []
    S_real_imag_list_new = []
    
    Phi_real_imag_H_list = tf.concat([tf.transpose(Phi_real_imag_list[:,:,:,:,0:1],(0,1,3,2,4)),-tf.transpose(Phi_real_imag_list[:,:,:,:,1:2],(0,1,3,2,4))],axis=-1)
    term1_list = Phi_real_imag_list[:,:,:,:,0]**2+Phi_real_imag_list[:,:,:,:,1]**2
    
    for i in range(num_sc):
        Tau_p = tf.matmul(term1_list[:,i],Tau_x_list[:,i])
        P_real_imag = complex_matrix_multiplication(Phi_real_imag_list[:,i], X_hat_real_imag_list[:,i]) - tf.expand_dims(Tau_p,axis=-1) * S_real_imag_list[:,i]
        Tau_s = 1 / (Tau_p + 1 / tf.expand_dims(beta,axis=-1)*tf.ones_like(Tau_p))
        S_real_imag = tf.expand_dims(Tau_s,axis=-1) * (y_real_imag_list[:,i] - P_real_imag)
        Tau_q = 1 / tf.matmul(tf.transpose(term1_list[:,i],(0,2,1)), Tau_s)
        Q_real_imag = X_hat_real_imag_list[:,i] + tf.expand_dims(Tau_q,axis=-1) * complex_matrix_multiplication(Phi_real_imag_H_list[:,i], S_real_imag)
        Tau_x = Tau_q * alpha_hat_list[:,i] / (alpha_hat_list[:,i] + Tau_q)
        X_hat_real_imag = Q_real_imag * tf.expand_dims(alpha_hat_list[:,i],axis=-1) / tf.expand_dims(alpha_hat_list[:,i] + Tau_q, axis=-1)

        Tau_x_list_new.append(tf.expand_dims(Tau_x,axis=1))
        X_hat_real_imag_list_new.append(tf.expand_dims(X_hat_real_imag,axis=1))
        S_real_imag_list_new.append(tf.expand_dims(S_real_imag,axis=1))
        
    Tau_x_list_new = tf.concat(Tau_x_list_new,axis=1)
    X_hat_real_imag_list_new = tf.concat(X_hat_real_imag_list_new,axis=1)
    S_real_imag_list_new = tf.concat(S_real_imag_list_new,axis=1)
    
    return Tau_x_list_new, X_hat_real_imag_list_new, S_real_imag_list_new


#%% construct the network
def SBL_net(Mr_max, Nr, G, G_angle, num_sc, num_layers, num_filters, kernel_size):     
    # def select_weight(config):
    #     config1,config2 = config
    #     if tf.equal(config1,tf.constant(1)) & tf.equal(config2,tf.constant(1)):
    #         weight = 1
    #     else:
    #         weight = 1
    #     return weight
    #
    # def weighted_nmse(y_true, y_pred):
    #     H_pred = y_pred[0]
    #     nmse = tf.keras.losses.MeanSquaredError()(y_true, H_pred) / tf.keras.backend.mean(
    #         tf.keras.backend.square(y_true))
    #
    #     config = tf.cast(y_pred[1], tf.int32)
    #     weight = tf.map_fn(select_weight,(config[:,0],config[:,1]))
    #
    #     loss = nmse * weight
    #
    #     return loss

    def weighted_nmse(y_true, y_pred):
        h_pred = y_pred[:,:-1]
        weight = y_pred[:,-1:]
        loss = weight * tf.reduce_mean(tf.square(y_true-h_pred),axis=-1,keepdims=True)/tf.reduce_mean(tf.square(y_true),axis=-1,keepdims=True)
        return loss

    y_real_imag_0 = Input(shape=(Mr_max, num_sc, 2))
    Mr = Input(shape=(1,))
    SNR = Input(shape=(1,))
    loss_weight_list = Input(shape=(1,))

    y_real_imag_list = tf.expand_dims(y_real_imag_0,axis=-2)
    # (?,num_sc,Mr,1,2)
    y_real_imag_list = tf.transpose(y_real_imag_list,(0,2,1,3,4))
    
    Phi_real_imag_list = Fixed_Phi_Layer_FR_dynamic_Mr(num_sc, Mr_max, G)(Mr)
    
    sigma_2 = 1 / 10 ** (SNR / 10) 
    beta = 1 / sigma_2

    config = tf.concat([Mr/8-3,SNR/5+1],axis=-1) # both elements are in [1,2,3,4,5]

    # Initialization
    # (?,num_sc,G,1)
    Tau_x_list = tf.tile(tf.ones_like(y_real_imag_0[:, 0:1, 0:1, 0:1]), (1, num_sc, G, 1))

    # (?,num_sc,G,1,2)
    X_hat_real_imag_list = tf.tile(tf.zeros_like(y_real_imag_list[:, 0:1, 0:1, 0:1, 0:1]), (1, num_sc, G, 1, 2)) # complex
    # (?,num_sc,G,1)
    alpha_hat_list = tf.tile(tf.ones_like(y_real_imag_0[:, 0:1, 0:1, 0:1]), (1, num_sc, G, 1))
    # (?,num_sc,Mr,1,2)
    S_real_imag_list = tf.tile(tf.zeros_like(y_real_imag_list[:, 0:1, 0:1, 0:1, 0:1]), (1, num_sc, Mr_max, 1, 2)) # complex

    # update mu and Sigma
    Tau_x_list, X_hat_real_imag_list, S_real_imag_list = update_UAMP_SBL(Phi_real_imag_list, y_real_imag_list, Tau_x_list, X_hat_real_imag_list, alpha_hat_list, S_real_imag_list, beta)

    model_list = []

    for i in range(num_layers):
        tmp = Dense(name='Dense_%d1'%i,units=num_filters*4,activation='relu')(config)
        attention = Dense(name='Dense_%d2'%i,units=num_filters,activation='sigmoid')(tmp)
        attention = tf.reshape(attention,(-1,1,1,num_filters))
        
        mu_square_list = Lambda(lambda x: x[0] ** 2 + x[1] ** 2)([X_hat_real_imag_list[:,:,:,:,0], X_hat_real_imag_list[:,:,:,:,1]])

        # feature tensor of dim (?,num_sc,G,2)
        temp = Lambda(lambda x: tf.concat(x, axis=-1))([mu_square_list,Tau_x_list])

        conv_layer1 = Conv2D(name='SBL_%d1'%i,filters=num_filters,kernel_size=kernel_size,strides=1,padding='same',activation='relu')
        conv_layer2 = Conv2D(name='SBL_%d2'%i,filters=1,kernel_size=kernel_size,strides=1,padding='same',activation='relu')

        temp = tf.reshape(temp,(-1,num_sc,G//G_angle,G_angle,2)) # (?,num_sc,G//G_angle,G_angle,2)

        temp = conv_layer1(temp) # (?,num_sc,G//G_angle,G_angle,num_filters)

        # average 
        temp = tf.reduce_mean(temp,axis=1,keepdims=False) # (?,G//G_angle,G_angle,num_filters)

        # dynamic re-weighting
        temp = temp*attention
 
        temp = conv_layer2(temp) # (?,G//G_angle,G_angle,1)
        
        temp = tf.reshape(temp,(-1,1,G,1)) # (?,1,G,1)
        
        alpha_hat_list = tf.tile(temp,(1,num_sc,1,1)) # (?,num_sc,G,1)
        
        # update mu and Sigma
        Tau_x_list, X_hat_real_imag_list, S_real_imag_list = update_UAMP_SBL(Phi_real_imag_list, y_real_imag_list, Tau_x_list, X_hat_real_imag_list, alpha_hat_list, S_real_imag_list, beta)

        H_hat = A_R_Layer_FR(Nr, G, num_sc)(tf.transpose(X_hat_real_imag_list[:, :, :, 0, :], (0, 2, 1, 3)))

        outputs = tf.concat([tf.reshape(H_hat,(-1,Nr*num_sc*2)),loss_weight_list],axis=-1)
        
        model = Model(inputs=[y_real_imag_0,Mr,SNR,loss_weight_list], outputs=outputs)
        
        model.compile(loss=weighted_nmse, optimizer=Adam(learning_rate=1e-3))
        
        model_list.append(model)
            
    return model_list

num_layers = 10
num_filters = 16
kernel_size = 5

model_list = SBL_net(Mr_max, Nr, G_polar, G_angle, num_sc, num_layers, num_filters, kernel_size)

epochs = 200 # 1000/200
batch_size = 16

# weight initialization
init_weights_R = C2R(A_list_polar)

init_weights_Phi = []
for Mr in Mr_collection:
    Phi_list_polar = Phi_list_polar_dict['%s'%Mr]
    init_weights_Phi.append(C2R(Phi_list_polar))

model_count = 0
model_count_start = 2 # 0/2/num_layers-1

print('Totally %d layers, start training from %d layers'%(num_layers,model_count_start+1))

for model in model_list[model_count_start:]:
    # different weight initialization for different models, block wise inherent
    if model_count == 0:
        for layer in model.layers:
            if 'a_r_' in layer.name:
                print('Set A_R weights')
                layer.set_weights([init_weights_R])
            if 'phi_' in layer.name:
                print('Set Phi weights')
                layer.set_weights(init_weights_Phi)

    else:
        conv_count = 0
        dense_count = 0
        for layer in model.layers:
            if 'a_r_' in layer.name:
                print('Set A_R weights')
                layer.set_weights([init_weights_R])
            if 'phi_' in layer.name:
                print('Set Phi weights')
                layer.set_weights(init_weights_Phi)
            if 'SBL_' in layer.name:
                print('Set Conv weights')
                layer.set_weights(weight_list_conv[conv_count])
                conv_count = conv_count + 1
            if 'Dense_' in layer.name:
                print('Set Dense weights')
                layer.set_weights(weight_list_dense[dense_count])
                dense_count = dense_count + 1       

    model_count = model_count + 1

    # Training
    # define callbacks
    best_model_path = './models/%dLayers_mixed.h5'%(model_count_start+model_count)
    checkpointer = ModelCheckpoint(best_model_path, verbose=1, save_best_only=True, save_weights_only=True)
    reduce_lr = ReduceLROnPlateau(monitor='val_loss', factor=0.1, patience=4, verbose=1, mode='auto',
                                  min_delta=1e-5, min_lr=1e-5)
    early_stopping = EarlyStopping(monitor='val_loss', min_delta=1e-5, patience=10)

    # model.summary()
#    if model_count > 1:
    model.fit([Y_real_imag_list,Mr_list,SNR_list,loss_weight_list], H_real_imag_list, epochs=epochs, batch_size=batch_size,
              verbose=1, shuffle=True, \
              validation_split=0.1, callbacks=[checkpointer, reduce_lr, early_stopping])

    model.load_weights(best_model_path)

    # creat the initial conv weight list for the next model
    weight_list_conv = []
    weight_list_dense = []
    for layer in model.layers:
        if 'SBL_' in layer.name:
            print('Save Conv weights')
            weight_list_conv.append(layer.get_weights())
        if 'Dense_' in layer.name:
            print('Save Dense weights')
            weight_list_dense.append(layer.get_weights())

    weight_list_conv = weight_list_conv + weight_list_conv[-2:]
    weight_list_dense = weight_list_dense + weight_list_dense[-2:]
    print('Copy the trainable Conv and Dense weights from the previous AMP-SBL layer')

